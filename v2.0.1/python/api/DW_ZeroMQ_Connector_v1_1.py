# -*- coding: utf-8 -*-
"""
    DWX_ZeroMQ_Connector_v1_0.py
    --
    @author: Darwinex Labs (www.darwinex.com)
    Last Updated: August 06, 2019
    Copyright (c) 2017-2019, Darwinex. All rights reserved.
    Licensed under the BSD 3-Clause License, you may not use this file except
    in compliance with the License.
    You may obtain a copy of the License at
    https://opensource.org/licenses/BSD-3-Clause
"""
import zmq
from time import sleep, mktime
from pandas import DataFrame, Timestamp
from threading import Thread
from zmq.utils.monitor import recv_monitor_message

# ENUM_DWX_SERV_ACTION
# NOTE: There is not too many actions and all could be replaced
# by one number. No need to send and process two strings: TRADE|DATA and ACTION.
HEARTBEAT=0
POS_OPEN=1
POS_MODIFY=2
POS_CLOSE=3
POS_CLOSE_PARTIAL=4
POS_CLOSE_MAGIC=5
POS_CLOSE_ALL=6
ORD_OPEN=7
ORD_MODIFY=8
ORD_DELETE=9
ORD_DELETE_ALL=10
GET_POSITIONS=11
GET_PENDING_ORDERS=12
GET_DATA=13
GET_TICK_DATA=14
GET_DATA_SYMBOL=15
GET_ALL_SYMBOLS=16

class DWX_ZeroMQ_Connector():
    """
    Setup ZeroMQ -> MetaTrader Connector
    """
    def __init__(self,
                 _ClientID='dwx_jmar',    # Unique ID for this client
                 _host='127.0.0.1',         # Host to connect to, localhost
                 _protocol='tcp',           # Connection protocol
                 _PUSH_PORT=32766,          # Port for Sending commands
                 _PULL_PORT=32767,          # Port for Receiving responses
                 _SUB_PORT=32770,           # Port for Subscribing for prices
                 _delimiter=';',            # String delimiter
                 _verbose=False,            # Print all responses(self._DWX_ZMQ_Poll_Data_)
                 _poll_timeout=1000,        # ZMQ Poller Timeout (ms)
                 _sleep_delay=0.001,        # 1 ms for time.sleep()
                 _monitor=False):           # Experimental ZeroMQ Socket Monitoring
        ######################################################################
        # Strategy Status (if this is False, ZeroMQ will not listen for data)
        self._ACTIVE = True
        # Client ID
        self._ClientID = _ClientID
        # ZeroMQ Host
        self._host = _host
        # Connection Protocol
        self._protocol = _protocol
        # ZeroMQ Context
        self._ZMQ_CONTEXT = zmq.Context()
        # TCP Connection URL Template
        self._URL = self._protocol + "://" + self._host + ":"
        # Ports for PUSH, PULL and SUB sockets respectively
        self._PUSH_PORT = _PUSH_PORT
        self._PULL_PORT = _PULL_PORT
        self._SUB_PORT = _SUB_PORT
        # Create Sockets
        self._PUSH_SOCKET = self._ZMQ_CONTEXT.socket(zmq.PUSH)
        self._PUSH_SOCKET.setsockopt(zmq.SNDHWM, 1)
        self._PUSH_SOCKET_STATUS = {'state': True, 'latest_event': 'N/A'}
        self._PULL_SOCKET = self._ZMQ_CONTEXT.socket(zmq.PULL)
        self._PULL_SOCKET.setsockopt(zmq.RCVHWM, 1)
        self._PULL_SOCKET_STATUS = {'state': True, 'latest_event': 'N/A'}
        self._SUB_SOCKET = self._ZMQ_CONTEXT.socket(zmq.SUB)
        # Bind PUSH Socket to send commands to MetaTrader
        self._PUSH_SOCKET.connect(self._URL + str(self._PUSH_PORT))
        #print("[INIT] Ready to send commands to METATRADER (PUSH): " + str(self._PUSH_PORT))
        # Connect PULL Socket to receive command responses from MetaTrader
        self._PULL_SOCKET.connect(self._URL + str(self._PULL_PORT))
        #print("[INIT] Listening for responses from METATRADER (PULL): " + str(self._PULL_PORT))
        # Connect SUB Socket to receive market data from MetaTrader
        #print("[INIT] Listening for market data from METATRADER (SUB): " + str(self._SUB_PORT))
        self._SUB_SOCKET.connect(self._URL + str(self._SUB_PORT))
        # Initialize POLL set and register PULL and SUB sockets
        self._poller = zmq.Poller()
        self._poller.register(self._PULL_SOCKET, zmq.POLLIN)
        self._poller.register(self._SUB_SOCKET, zmq.POLLIN)
        # Start listening for responses to commands and new market data
        self._string_delimiter = _delimiter
        # Tick data packages (time in ms, Ask price, Bid price) delimiter
        self._packet_data_delimiter="#"
        # BID/ASK Market Data Subscription Threads ({SYMBOL: Thread})
        self._MarketData_Thread = None
        # Socket Monitor Threads
        self._PUSH_Monitor_Thread = None
        self._PULL_Monitor_Thread = None
        # Market Data Dictionary by Symbol (holds tick data)
        self._Market_Data_DB = {}   # {SYMBOL: {TIMESTAMP: (BID, ASK)}}
        # Temporary Order STRUCT for convenience wrappers later.
        self.temp_order_dict = self._generate_default_order_dict()
        # Thread returns the most recently received DATA block here
        self._thread_data_output = None
        # Verbosity
        self._verbose = _verbose
        # ZMQ Poller Timeout
        self._poll_timeout = _poll_timeout
        # Global Sleep Delay
        self._sleep_delay = _sleep_delay
        # Begin polling for PULL / SUB data
        self._MarketData_Thread = Thread(target=self._DWX_ZMQ_Poll_Data_,
                                         args=(self._string_delimiter,
                                               self._packet_data_delimiter,
                                               self._poll_timeout,))
        self._MarketData_Thread.daemon = True
        self._MarketData_Thread.start()
        ###########################################
        # Enable/Disable ZeroMQ Socket Monitoring #
        ###########################################
        if _monitor == True:
            # ZeroMQ Monitor Event Map
            self._MONITOR_EVENT_MAP = {}
            print("\n[KERNEL] Retrieving ZeroMQ Monitor Event Names:\n")
            for name in dir(zmq):
                if name.startswith('EVENT_'):
                    value = getattr(zmq, name)
                    print("{value}\t\t:\t{name}")
                    self._MONITOR_EVENT_MAP[value] = name
            print("\n[KERNEL] Socket Monitoring Config -> DONE!\n")
            # Disable PUSH/PULL sockets and let MONITOR events control them.
            self._PUSH_SOCKET_STATUS['state'] = False
            self._PULL_SOCKET_STATUS['state'] = False
            # PUSH
            self._PUSH_Monitor_Thread = Thread(target=self._DWX_ZMQ_EVENT_MONITOR_,
                                               args=("PUSH",
                                                     self._PUSH_SOCKET.get_monitor_socket(),))
            self._PUSH_Monitor_Thread.daemon = True
            self._PUSH_Monitor_Thread.start()
            # PULL
            self._PULL_Monitor_Thread = Thread(target=self._DWX_ZMQ_EVENT_MONITOR_,
                                               args=("PULL",
                                                     self._PULL_SOCKET.get_monitor_socket(),))
            self._PULL_Monitor_Thread.daemon = True
            self._PULL_Monitor_Thread.start()

    ##########################################################################
    def _DWX_ZMQ_SHUTDOWN_(self):
        # Set INACTIVE
        self._ACTIVE = False
        # Get all threads to shutdown
        if self._MarketData_Thread is not None:
            self._MarketData_Thread.join()
        if self._PUSH_Monitor_Thread is not None:
            self._PUSH_Monitor_Thread.join()
        if self._PULL_Monitor_Thread is not None:
            self._PULL_Monitor_Thread.join()
        # Unregister sockets from Poller
        self._poller.unregister(self._PULL_SOCKET)
        self._poller.unregister(self._SUB_SOCKET)
        #print("\n++ [KERNEL] Sockets unregistered from ZMQ Poller()! ++")
        # Terminate context 
        self._ZMQ_CONTEXT.destroy(0)
        #print("\n++ [KERNEL] ZeroMQ Context Terminated.. shut down safely complete! :)")
    ##########################################################################
    def _setStatus(self, _new_status=False):
        """
        Set Status (to enable/disable strategy manually)
        """
        self._ACTIVE = _new_status
        print("\n**\n[KERNEL] Setting Status to {} - Deactivating Threads.. please wait a bit.\n**".format(_new_status))
    ##########################################################################
    def remote_send(self, _socket, _data):
        """
        Function to send commands to MetaTrader (PUSH)
        """
        if self._PUSH_SOCKET_STATUS['state'] == True:
            try:
                _socket.send_string(_data, zmq.DONTWAIT)
            except zmq.error.Again:
                print("\nResource timeout.. please try again.")
                sleep(self._sleep_delay)
        else:
            print('\n[KERNEL] NO HANDSHAKE ON PUSH SOCKET.. Cannot SEND data')
    ##########################################################################
    def _get_response_(self):
        return self._thread_data_output
    ##########################################################################
    def _set_response_(self, _resp=None):
        self._thread_data_output = _resp
    ##########################################################################
    def _valid_response_(self, _input='zmq'):
        # Valid data types
        _types = (dict,DataFrame)
        # If _input = 'zmq', assume self._zmq._thread_data_output
        if isinstance(_input, str) and _input == 'zmq':
            return isinstance(self._get_response_(), _types)
        else:
            return isinstance(_input, _types)
        # Default
        return False
    ##########################################################################
    def remote_recv(self, _socket):
        """
        Function to retrieve data from MetaTrader (PULL)
        """
        if self._PULL_SOCKET_STATUS['state'] == True:
            try:
                msg = _socket.recv_string(zmq.DONTWAIT)
                return msg
            except zmq.error.Again:
                print("\nResource timeout.. please try again.")
                sleep(self._sleep_delay)
        else:
            print('[KERNEL] NO HANDSHAKE ON PULL SOCKET.. Cannot READ data')
        return None
    ##########################################################################
    # Convenience functions to permit easy trading via underlying functions. #
    ##########################################################################
    ##########################################################################
    # NEW POSITION OR PENDING ORDER
    def _DWX_MTX_NEW_TRADE_(self, _order=None):
        if _order is None:
            _order = self._generate_default_order_dict()
        # Execute
        self._DWX_MTX_SEND_COMMAND_(**_order)
    ##########################################################################
    # MODIFY POSITION (SET|RESET|UPDATE SL|TP)
    def _DWX_MTX_MODIFY_POSITION_BY_TICKET_(self, _ticket, _SL, _TP): # in points
        try:
            self.temp_order_dict['_action'] = POS_MODIFY
            self.temp_order_dict['_SL'] = _SL
            self.temp_order_dict['_TP'] = _TP
            self.temp_order_dict['_ticket'] = _ticket
            # Execute
            self._DWX_MTX_SEND_COMMAND_(**self.temp_order_dict)
        except KeyError:
            print("[ERROR] Order Ticket {} not found!".format(_ticket))
    ##########################################################################
    # MODIFY PENDING ORDER (SET|RESET|UPDATE SL|TP)
    def _DWX_MTX_MODIFY_ORDER_BY_TICKET_(self, _ticket, _SL, _TP): # in points
        try:
            self.temp_order_dict['_action'] = ORD_MODIFY
            self.temp_order_dict['_SL'] = _SL
            self.temp_order_dict['_TP'] = _TP
            self.temp_order_dict['_ticket'] = _ticket
            # Execute
            self._DWX_MTX_SEND_COMMAND_(**self.temp_order_dict)
        except KeyError:
            print("[ERROR] Order Ticket {} not found!".format(_ticket))
    ##########################################################################
    # CLOSE POSITION BY TICKET
    def _DWX_MTX_CLOSE_POSITION_BY_TICKET_(self, _ticket):
        try:
            self.temp_order_dict['_action'] = POS_CLOSE
            self.temp_order_dict['_ticket'] = _ticket
            # Execute
            self._DWX_MTX_SEND_COMMAND_(**self.temp_order_dict)
        except KeyError:
            print("[ERROR] Order Ticket {} not found!".format(_ticket))
    ##########################################################################
    # DELETE PENDING ORDER BY TICKET
    def _DWX_MTX_DELETE_PENDING_BY_TICKET_(self, _ticket):
        try:
            self.temp_order_dict['_action'] = ORD_DELETE
            self.temp_order_dict['_ticket'] = _ticket
            # Execute
            self._DWX_MTX_SEND_COMMAND_(**self.temp_order_dict)
        except KeyError:
            print("[ERROR] Order Ticket {} not found!".format(_ticket))
    ##########################################################################
    # CLOSE PARTIAL
    def _DWX_MTX_CLOSE_PARTIAL_BY_TICKET_(self, _ticket, _lots):
        try:
            self.temp_order_dict['_action'] = POS_CLOSE_PARTIAL
            self.temp_order_dict['_ticket'] = _ticket
            self.temp_order_dict['_lots'] = _lots
            # Execute
            self._DWX_MTX_SEND_COMMAND_(**self.temp_order_dict)
        except KeyError:
            print("[ERROR] Order Ticket {} not found!".format(_ticket))
    ##########################################################################
    # CLOSE MAGIC
    def _DWX_MTX_CLOSE_POSITIONS_BY_MAGIC_(self, _magic):
        try:
            self.temp_order_dict['_action'] = POS_CLOSE_MAGIC
            self.temp_order_dict['_magic'] = _magic
            # Execute
            self._DWX_MTX_SEND_COMMAND_(**self.temp_order_dict)
        except KeyError:
            pass
    ##########################################################################
    # CLOSE ALL POSITIONS
    def _DWX_MTX_CLOSE_ALL_POSITIONS_(self):
        try:
            self.temp_order_dict['_action'] = POS_CLOSE_ALL
            # Execute
            self._DWX_MTX_SEND_COMMAND_(**self.temp_order_dict)
        except KeyError:
            pass
    ##########################################################################
    # DELETE ALL PENDING ORDERS
    def _DWX_MTX_DELETE_ALL_PENDING_(self):
        try:
            self.temp_order_dict['_action'] = ORD_DELETE_ALL
            # Execute
            self._DWX_MTX_SEND_COMMAND_(**self.temp_order_dict)
        except KeyError:
            pass
    ##########################################################################
    # GET WORKING POSITIONS
    def _DWX_MTX_GET_ALL_OPEN_POSITIONS_(self):
        try:
            self.temp_order_dict['_action'] = GET_POSITIONS
            # Execute
            self._DWX_MTX_SEND_COMMAND_(**self.temp_order_dict)
        except KeyError:
            pass
    ##########################################################################
    # GET PENDING ORDERS
    def _DWX_MTX_GET_ALL_PENDING_ORDERS_(self):
        try:
            self.temp_order_dict['_action'] = GET_PENDING_ORDERS
            # Execute
            self._DWX_MTX_SEND_COMMAND_(**self.temp_order_dict)
        except KeyError:
            pass
    ##########################################################################
    # DEFAULT ORDER DICT
    def _generate_default_order_dict(self):
        return({'_action': POS_OPEN,
                  '_type': 0,
                  '_symbol': 'EURUSD',
                  '_price': 0.0,
                  '_SL': 500, # SL/TP in POINTS, not pips.
                  '_TP': 500,
                  '_comment': self._ClientID,
                  '_lots': 0.01,
                  '_magic': 123456,
                  '_ticket': 0})
    ##########################################################################
    # DEFAULT DATA REQUEST DICT
    def _generate_default_data_dict(self):
        return({'_action': GET_DATA,
                  '_symbol': 'EURUSD',
                  # MT5 ENUM_TIMEFRAMES to int
                  # M1: 1, M2: 2, M3: 3, M4: 4, M5: 5, M6: 6, M10: 10, M12: 12,
                  # M15: 15, M20: 20, M30: 30, H1: 16385, H2: 16386, H3: 16387,
                  # H4: 16388, H6: 16390, H8: 16392, H12: 16396, D1: 16408,
                  # W1: 32769, MN1: 49153
                  '_timeframe': 16385,
                  '_start': '2019.12.02 17:00:00', # timestamp in MT5 recognized format
                  '_end': '2019.12.03 17:00:00'})
    ##########################################################################
    def _DWX_MTX_SEND_MARKETDATA_REQUEST_(self,
                                 _action=GET_DATA,
                                 _symbol='EURUSD',
                                 _timeframe=16385,
                                 _start=Timestamp.now().strftime('%Y.%m.%d 00:00:00'),
                                 _end=Timestamp.now().strftime('%Y.%m.%d %H:%M:00')):
        _msg = "{};{};{};{};{}".format(_action,
                                    _symbol,
                                     _timeframe,
                                     _start,
                                     _end)
        """
        Function to construct messages for sending DATA commands to MetaTrader
        """
        # Send via PUSH Socket
        self.remote_send(self._PUSH_SOCKET, _msg)
    ##########################################################################
    def _DWX_MTX_SEND_SYMBOL_DATA_REQUEST_(self,
                                 _action=GET_DATA_SYMBOL,
                                 _symbol='EURUSD'):
        _msg = "{};{}".format(_action,
                                    _symbol)
        """
        Function to construct messages for getting DATA SYMBOL commands to MetaTrader
        Responses:
        SYMBOL NOT FOUND (NULL)
        {'_action': 'GET_DATA_SYMBOL', '_symbol_data': {'_symbol_point': 0.0, '_symbol_digits': 0, '_symbol_contract_size': 0.0, '_symbol_lots_min': 0.0}}
        EURUSD
        {'_action': 'GET_DATA_SYMBOL', '_symbol_data': {'_symbol_point': 1e-05, '_symbol_digits': 5, '_symbol_contract_size': 100000.0, '_symbol_lots_min': 0.01}}
        LTCUSD
        {'_action': 'GET_DATA_SYMBOL', '_symbol_data': {'_symbol_point': 0.01, '_symbol_digits': 2, '_symbol_contract_size': 1.0, '_symbol_lots_min': 0.05}}
        USDJPY
        {'_action': 'GET_DATA_SYMBOL', '_symbol_data': {'_symbol_point': 0.001, '_symbol_digits': 3, '_symbol_contract_size': 100000.0, '_symbol_lots_min': 0.01}}
        TSLA
        {'_action': 'GET_DATA_SYMBOL', '_symbol_data': {'_symbol_point': 0.01, '_symbol_digits': 2, '_symbol_contract_size': 1.0, '_symbol_lots_min': 0.1}}
        US500
        {'_action': 'GET_DATA_SYMBOL', '_symbol_data': {'_symbol_point': 0.01, '_symbol_digits': 2, '_symbol_contract_size': 1.0, '_symbol_lots_min': 0.1}}
        """
        # Send via PUSH Socket
        self.remote_send(self._PUSH_SOCKET, _msg)
    ##########################################################################
    def _DWX_MTX_GET_ALL_SYMBOLS_REQUEST_(self,
                                 _action=GET_ALL_SYMBOLS,
                                 _symbol='EURUSD'):
        _msg = "{};{}".format(_action,
                                    _symbol)
        """
        Function to construct messages for getting A LIST WITH ALL SYMBOLS in MetaTrader5
        Responses:
        """
        # Send via PUSH Socket
        self.remote_send(self._PUSH_SOCKET, _msg)
    ##########################################################################
    def _DWX_MTX_SEND_MARKET_TICKDATA_REQUEST_(self,
                                 _action=GET_TICK_DATA,
                                 _symbol='EURUSD',
                                 _start=Timestamp.now().strftime('%Y.%m.%d 00:00:00'),
                                 _end=Timestamp.now().strftime('%Y.%m.%d %H:%M:00')):
        _msg = "{};{};{};{}".format(_action,
                                    _symbol,
                                     _start,
                                     _end)
        """
    Function to construct messages for sending TICK DATA commands to MetaTrader
    Be carefull while setting _start|_end: there might be hundreds of thousands
    of ticks per day.
        """
        # Send via PUSH Socket
        self.remote_send(self._PUSH_SOCKET, _msg)
    ##########################################################################
    def _DWX_MTX_SEND_COMMAND_(self, _action=POS_OPEN, _type=0,
                                 _symbol='EURUSD', _price=0.0,
                                 _SL=50, _TP=50, _comment="Python-to-MT",
                                 _lots=0.01, _magic=123456, _ticket=0):
        _msg = "{};{};{};{};{};{};{};{};{};{}".format(_action,_type,
                                                         _symbol,_price,
                                                         _SL,_TP,_comment,
                                                         _lots,_magic,
                                                         _ticket)
        """
    Function to construct messages for sending Trade commands to MetaTrader
        """
        # Send via PUSH Socket
        self.remote_send(self._PUSH_SOCKET, _msg)
        """
         IMPORTANT NOTE: size of compArray is 10, not 11 as in original version!

         compArray[0] = ACTION (e.g. POS_OPEN|POS_MODIFY|POS_CLOSE|POS_CLOSE_PARTIAL
         |POS_CLOSE_MAGIC|POS_CLOSE_ALL|ORD_OPEN|ORD_MODIFY|ORD_DELETE|ORD_DELETE_ALL)

         compArray[1] = TYPE (e.g. ORDER_TYPE_BUY|ORDER_TYPE_SELL only used when ACTION=POS_OPEN,
         and ORDER_TYPE_BUY_LIMIT|ORDER_TYPE_SELL_LIMIT|ORDER_TYPE_BUY_STOP|ORDER_TYPE_SELL_STOP
         only used when ACTION=ORD_OPEN)

         For compArray[0] == GET_DATA, format is:
             GET_DATA|SYMBOL|TIMEFRAME|START_DATETIME|END_DATETIME
         ORDER TYPES:
         https://www.mql5.com/en/docs/constants/tradingconstants/orderproperties#enum_order_type
         ORDER_TYPE_BUY = 0
         ORDER_TYPE_SELL = 1
         ORDER_TYPE_BUY_LIMIT = 2
         ORDER_TYPE_SELL_LIMIT = 3
         ORDER_TYPE_BUY_STOP = 4
         ORDER_TYPE_SELL_STOP = 5

         compArray[2] = Symbol (e.g. EURUSD, etc.)
         compArray[3] = Open/Close Price (ignored if ACTION = MODIFY)
         compArray[4] = SL
         compArray[5] = TP
         compArray[6] = Trade Comment
         compArray[7] = Lots
         compArray[8] = Magic Number
         compArray[9] = Ticket Number (MODIFY/CLOSE)
         """
        # pass
    ##########################################################################
    def _DWX_ZMQ_Poll_Data_(self,
                           string_delimiter=';',
                           packet_data_delimiter='#',
                           poll_timeout=1000):
        """
    Function to check Poller for new reponses (PULL) and market data (SUB)
    IMPORTANT: To read the responses by the PULL socket via the var
    "self._thread_data_output" is necesary a little delay after the execution
    by any of the sending commands, in my case
        (bot)localhost --> (Metatrader5)localhost
            "sleep(0.02)"
        """
        while self._ACTIVE:
            sleep(self._sleep_delay) # poll timeout is in ms, sleep() is s.
            sockets = dict(self._poller.poll(poll_timeout))
            # Process response to commands sent to MetaTrader
            if self._PULL_SOCKET in sockets and sockets[self._PULL_SOCKET] == zmq.POLLIN:
                if self._PULL_SOCKET_STATUS['state'] == True:
                    try:
                        # msg = self._PULL_SOCKET.recv_string(zmq.DONTWAIT)
                        msg = self.remote_recv(self._PULL_SOCKET)
                        # If data is returned, store as pandas Series
                        if msg != '' and msg != None:
                            try:
                                _data = eval(msg)
                                self._thread_data_output = _data
                                if self._verbose:
                                    print(_data) # default logic
                            except Exception as ex:
                                _exstr = "Exception Type {0}. Args:\n{1!r}"
                                _msg = _exstr.format(type(ex).__name__, ex.args)
                                print(_msg)
                    except zmq.error.Again:
                        pass # resource temporarily unavailable, nothing to print
                    except ValueError:
                        pass # No data returned, passing iteration.
                    except UnboundLocalError:
                        pass # _symbol may sometimes get referenced before being assigned.
                else:
                    print('\r[KERNEL] NO HANDSHAKE on PULL SOCKET.. Cannot READ data.')
            # Receive new market data from MetaTrader
            if self._SUB_SOCKET in sockets and sockets[self._SUB_SOCKET] == zmq.POLLIN:
                try:
                    msg = self._SUB_SOCKET.recv_string(zmq.DONTWAIT)
                    if msg != "":
                        _symbol, _data = msg.split(" ")
                        # There might be one or more ticks data and need bo be split
                        _packets = _data.split(packet_data_delimiter)
                        for _tick in _packets:
                            _timestamp, _bid, _ask = _tick.split(string_delimiter)
                            # Received time in milliseconds need to be formatted
                            _timestamp = str(Timestamp(int(_timestamp),unit='ms'))[:-3]
                            if self._verbose:
                                print("\n[" + _symbol + "] " + _timestamp + " (" + _bid + "/" + _ask + ") BID/ASK")
                            # Update Market Data DB
                            if _symbol not in self._Market_Data_DB.keys():
                                self._Market_Data_DB[_symbol] = {}
                            self._Market_Data_DB[_symbol][_timestamp] = (float(_bid), float(_ask))
                except zmq.error.Again:
                    pass # resource temporarily unavailable, nothing to print
                except ValueError:
                    pass # No data returned, passing iteration.
                except UnboundLocalError:
                    pass # _symbol may sometimes get referenced before being assigned.
        #print("\n++ [KERNEL] _DWX_ZMQ_Poll_Data_() Signing Out ++")
    ##########################################################################
    def _DWX_MTX_SUBSCRIBE_MARKETDATA_(self,
                                       _symbol='EURUSD',
                                       string_delimiter=';',
                                       poll_timeout=1000):
        """
    Function to subscribe to given Symbol's BID/ASK feed from MetaTrader
        """
        # Subscribe to SYMBOL first.
        self._SUB_SOCKET.setsockopt_string(zmq.SUBSCRIBE, _symbol)
        print("[KERNEL] Subscribed to {} BID/ASK updates. See self._Market_Data_DB.".format(_symbol))
    ##########################################################################
    def _DWX_MTX_UNSUBSCRIBE_MARKETDATA_(self, _symbol):
        """
    Function to unsubscribe to given Symbol's BID/ASK feed from MetaTrader
        """
        self._SUB_SOCKET.setsockopt_string(zmq.UNSUBSCRIBE, _symbol)
        print("\n**\n[KERNEL] Unsubscribing from " + _symbol + "\n**\n")
    ##########################################################################
    def _DWX_MTX_UNSUBSCRIBE_ALL_MARKETDATA_REQUESTS_(self):
        """
    Function to unsubscribe from ALL MetaTrader Symbols
        """
        # 31-07-2019 12:22 CEST
        for _symbol in self._Market_Data_DB.keys():
            self._DWX_MTX_UNSUBSCRIBE_MARKETDATA_(_symbol=_symbol)
    ##########################################################################
    def _DWX_ZMQ_EVENT_MONITOR_(self,
                                socket_name,
                                monitor_socket):
        # 05-08-2019 11:21 CEST
        while self._ACTIVE:
            sleep(self._sleep_delay) # poll timeout is in ms, sleep() is s.
            # while monitor_socket.poll():
            while monitor_socket.poll(self._poll_timeout):
                try:
                    evt = recv_monitor_message(monitor_socket, zmq.DONTWAIT)
                    evt.update({'description': self._MONITOR_EVENT_MAP[evt['event']]})
                    # print(f"\r[{socket_name} Socket] >> {evt['description']}", end='', flush=True)
                    print("\n[{socket_name} Socket] >> {evt['description']}")
                    # Set socket status on HANDSHAKE
                    if evt['event'] == 4096:        # EVENT_HANDSHAKE_SUCCEEDED
                        if socket_name == "PUSH":
                            self._PUSH_SOCKET_STATUS['state'] = True
                            self._PUSH_SOCKET_STATUS['latest_event'] = 'EVENT_HANDSHAKE_SUCCEEDED'
                        elif socket_name == "PULL":
                            self._PULL_SOCKET_STATUS['state'] = True
                            self._PULL_SOCKET_STATUS['latest_event'] = 'EVENT_HANDSHAKE_SUCCEEDED'
                        # print(f"\n[{socket_name} Socket] >> ..ready for action!\n")
                    else:
                        # Update 'latest_event'
                        if socket_name == "PUSH":
                            self._PUSH_SOCKET_STATUS['state'] = False
                            self._PUSH_SOCKET_STATUS['latest_event'] = evt['description']
                        elif socket_name == "PULL":
                            self._PULL_SOCKET_STATUS['state'] = False
                            self._PULL_SOCKET_STATUS['latest_event'] = evt['description']
                    if evt['event'] == zmq.EVENT_MONITOR_STOPPED:
                        # Reinitialize the socket
                        if socket_name == "PUSH":
                            monitor_socket = self._PUSH_SOCKET.get_monitor_socket()
                        elif socket_name == "PULL":
                            monitor_socket = self._PULL_SOCKET.get_monitor_socket()
                except Exception as ex:
                    _exstr = "Exception Type {0}. Args:\n{1!r}"
                    _msg = _exstr.format(type(ex).__name__, ex.args)
                    print(_msg)
        # Close Monitor Socket
        monitor_socket.close()
        print("\n++ [KERNEL] {socket_name} _DWX_ZMQ_EVENT_MONITOR_() Signing Out ++")
    ##########################################################################
    def _DWX_ZMQ_HEARTBEAT_(self):
        self.remote_send(self._PUSH_SOCKET, str(HEARTBEAT)+";")
##############################################################################
##############################################################################
def _DWX_ZMQ_CLEANUP_(_name='DWX_ZeroMQ_Connector',
                      _globals=globals(),
                      _locals=locals()):
    print('\n++ [KERNEL] Initializing ZeroMQ Cleanup.. if nothing appears below, no cleanup is necessary, otherwise please wait..')
    try:
        _class = _globals[_name]
        _locals = list(_locals.items())
        for _func, _instance in _locals:
            if isinstance(_instance, _class):
                print('\n++ [KERNEL] Found & Destroying {_func} object before __init__()')
                eval(_func)._DWX_ZMQ_SHUTDOWN_()
                print('\n++ [KERNEL] Cleanup Complete -> OK to initialize DWX_ZeroMQ_Connector if NETSTAT diagnostics == True. ++\n')
    except Exception as ex:
        _exstr = "Exception Type {0}. Args:\n{1!r}"
        _msg = _exstr.format(type(ex).__name__, ex.args)
        if 'KeyError' in _msg:
            print('\n++ [KERNEL] Cleanup Complete -> OK to initialize DWX_ZeroMQ_Connector. ++\n')
        else:
            print(_msg)
##############################################################################
if __name__ == '__main__':
    _zmq = DWX_ZeroMQ_Connector()
    #_zmq._DWX_MTX_GET_ALL_OPEN_POSITIONS_()
    #_zmq._DWX_MTX_CLOSE_POSITION_BY_TICKET_(_ticket=452309037)
    _zmq._DWX_ZMQ_HEARTBEAT_()
    x = None
    sleep(1)
    x = _zmq._get_response_()
    print(str(x))
    """if x:
        tickets = sorted([i for i in x['_positions']])
        for t in tickets:
            x['_positions'][t]['_exp'] = 3
            print(str(x['_positions'][t]))
    else:
        print('Server Fault')
    """
    _zmq._DWX_ZMQ_SHUTDOWN_()
