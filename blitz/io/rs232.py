__author__ = 'Will Hart'

import logging
import os
import threading
import time

from redis import ConnectionError
import serial
from serial.tools.list_ports import comports

from blitz.constants import SerialUpdatePeriod, SerialCommands
from blitz.data.database import DatabaseServer
from blitz.io.signals import logging_started, logging_stopped


class SerialManager(object):
    """
    Manages serial (eventually RS232, SPI or I2C) communications with
    expansion boards.  It has both a monitoring loop and an "outbox"
    which it uses for sending information.
    """

    __instance = None
    database = None
    serial_mapping = None
    __serial_thread = None

    logger = logging.getLogger(__name__)

    @classmethod
    def Instance(cls):
        """
        Returns a reference to a single SerialManager instance
        """
        cls.logger.debug("SerialManager Instance called")
        if cls.__instance is None:
            return SerialManager()
        else:
            return cls.__instance


    def __init__(self):
        """
        Follows a singleton pattern and prevents instantiation of more than one Serial Manager.
        """

        if SerialManager.__instance is not None:

            self.logger.error("Attempted to recreate an instance of a SerialManager - should use Instance()")
            raise Exception(
                "Attempted to instantiate a new SerialManager, but only one instance is"
                " allowed.  Use the Instance() method instead")
        else:
            self.logger.debug("SerialManager __init__")
            SerialManager.__instance = self

        # create a database object
        try:
            self.database = DatabaseServer()
        except ConnectionError as e:
            self.logger.critical("ConnectionError when attempting to start the DatabaseServer!")
            self.logger.critical(e)

        # work out which serial ports are connected
        self.get_available_ports()

        # register signals
        logging_started.connect(self.start)
        logging_stopped.connect(self.stop)

    def get_available_ports(self):
        """
        Generates a list of available serial ports, mapping their ID to
        the COM* or /dev/tty* reference.

        Adapted from http://stackoverflow.com/a/14224477/233608
        """
        self.logger.info("Scanning for available serial ports")
        self.serial_mapping = {}
        ports = []

        # Windows
        if os.name == 'nt':
            self.logger.debug("Performing Windows scan")
            # Scan for available ports.
            available = []
            for i in range(256):
                try:
                    portname = "COM%s" % (i + 1)
                    s = serial.Serial(portname)
                    s.close()
                    ports.append(portname)

                except serial.SerialException:
                    pass
        else:
            # Mac / Linux
            self.logger.debug("Performing Mac/Linux scan")
            for port in comports():
                ports.append(port[0])

        for port in ports:
            board_id = self.send_id_request(port)
            if board_id is not None:
                self.logger.info("Found board ID %s at %s" % (board_id, port))
                self.serial_mapping[hex(board_id)[2:].zfill(2)] = port

        return self.serial_mapping

    def create_serial_connection(self, port_name, baud_rate=57600, read_timeout=3):
        """
        Creates a serial port connection, opens it and returns it
        """
        return serial.Serial(port_name, baudrate=baud_rate, timeout=read_timeout)

    def receive_serial_data(self, board_id, port_name):
        """
        Requests a transmission from the specified board and
        saves the returned data to the database
        """
        port = self.create_serial_connection(port_name)

        # send the transmit request
        port.write(board_id + SerialCommands['TRANSMIT'] + "\n")

        # readlines until no more lines left (will read for the timeout period)
        lines = port.readlines()
        for line in lines:
            line_size = len(line)
            if line_size < 4:
                pass  # too short, ignore
            elif line_size == 4:
                # a short message
                command = line[2:]
                if command == SerialCommands['ACK']:
                    break  # all done, ignore the rest
            else:
                # a data message, save it for later
                self.database.queue(line)

        port.close()

    def send_id_request(self, port_name):
        """
        Requests an ID from the serial port name and returns it.
        If no ID is found, return None
        """
        port = self.create_serial_connection(port_name)
        board_id = None
        serial_buffer = ""

        # clear out any junk in the board's serial buffer and ignore the response
        port.write("\n")
        port.readline()

        # send the ID request
        port.write("00" + SerialCommands['ID'] + "\n")
        serial_buffer = port.readline()
        port.close()

        # check if a valid id was returned
        if len(serial_buffer) > 2:
            board_id = int(serial_buffer[0:2], 16)

        return board_id

    def send_command_with_ack(self, command, board_id, port_name):
        """
        Sends the given command over the serial port and checks for
        an ACK response.  Returns None if the ACK was received, and the
        received message otherwise
        """
        port = self.create_serial_connection(port_name)
        port.write(board_id + command)

        serial_buffer = port.readline()

        # TODO: properly handle errors

        if serial_buffer.length != 4 or serial_buffer[2:] != SerialCommands['ACK']:
            return serial_buffer

        return None

    def start(self, signal_args):
        """
        Starts listening on the serial ports and polling for updates every SerialUpdatePeriod seconds
        """

        # enter a new session
        session_id = self.database.start_session()

        # send a start signal to all boards
        for k in self.serial_mapping.keys():
            success = self.send_command_with_ack(SerialCommands['START'], k, self.serial_mapping[k])

            # log errors for now
            if not success is None:
                self.logger.warn("Did not receive a valid ACK response from board ID %s on START request" % k)

        # Start a thread for polling serial for updates
        self.__stop_event = threading.Event()
        self.__serial_thread = threading.Thread(target=self.__poll_serial, args=[self.__stop_event])
        self.__serial_thread.daemon = True
        self.__serial_thread.start()
        self.logger.debug("Started serial polling thread: %s" % self.__serial_thread.name)

        # log about serial listening starting
        self.logger.info("Commenced logging session %s" % session_id)

    def stop(self, signal_args):
        """Stops logging threads"""

        self.logger.debug("Received signal to stop logging")

        # send a stop signal to all boards
        for k in self.serial_mapping.keys():
            success = self.send_command_with_ack(SerialCommands['STOP'], k, self.serial_mapping[k])

            # log errors for now
            if not success is None:
                self.logger.warn("Did not receive a valid ACK response from board ID %s on STOP request" % k)

        # end the new session
        self.database.stop_session()
        self.__serial_thread.join()
        self.logger.info("Serial polling thread stopped")

    def __poll_serial(self, stop_event):
        """
        A thread which periodically polls a serial connection until a stop_event is received
        """
        while not stop_event.is_set():
            # enumerate each port
            for k in self.serial_mapping.keys():
                self.receive_serial_data(k, self.serial_mapping[k])

            time.sleep(SerialUpdatePeriod)

        self.logger.debug("Exited poll serial thread")
