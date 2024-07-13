#!/usr/bin/env python3

import asyncio
import serial_asyncio

import struct
import argparse
import json
import binascii
import atexit
import paho.mqtt.client as paho

# Command line arguments
parser = argparse.ArgumentParser(description='Fetches and outputs JBD bms data')
parser.add_argument("-s", "--serialport", help="Com port Address, e.g. /dev/ttyUSB0 or COM2", required=True)
parser.add_argument("-i", "--interval", type=int, help="Data fetch interval", required=True)
parser.add_argument("-m", "--meter", help="meter name", required=True)
args = parser.parse_args()
z = args.interval
comport = args.serialport
meter = args.meter

topic = "data/bms"
gauge = "data/bms/gauge"
broker = "192.168.1.22"
port = 1883
mqtt = paho.Client("control3")  # create and connect mqtt client


def disconnect():
    mqtt.disconnect()
    print("broker disconnected")


def cellinfo1(data):  # process pack info
    infodata = data
    i = 4  # Unpack into variables, skipping header bytes 0-3
    volts, amps, remain, capacity, cycles, mdate, balance1, balance2 = struct.unpack_from('>HhHHHHHH', infodata, i)
    volts = volts / 100
    amps = amps / 100
    capacity = capacity / 100
    remain = remain / 100
    watts = volts * amps  # adding watts field for dbase
    message1 = {
        "meter": "bms",
        "volts": volts,
        "amps": amps,
        "watts": watts,
        "remain": remain,
        "capacity": capacity,
        "cycles": cycles
    }
    ret = mqtt.publish(gauge, payload=json.dumps(message1), qos=0, retain=False)  # not sending mdate (manufacture date)

    bal1 = (format(balance1, "b").zfill(16))
    message2 = {
        "meter": "bms",  # using balance1 bits for 16 cells
        "c16": int(bal1[0:1]),
        "c15": int(bal1[1:2]),  # balance2 is for next 17-32 cells - not using
        "c14": int(bal1[2:3]),
        "c13": int(bal1[3:4]),
        "c12": int(bal1[4:5]),  # bit shows (0,1) charging on-off
        "c11": int(bal1[5:6]),
        "c10": int(bal1[6:7]),
        "c09": int(bal1[7:8]),
        "c08": int(bal1[8:9]),
        "c07": int(bal1[9:10]),
        "c06": int(bal1[10:11]),
        "c05": int(bal1[11:12]),
        "c04": int(bal1[12:13]),
        "c03": int(bal1[13:14]),
        "c02": int(bal1[14:15]),
        "c01": int(bal1[15:16])
    }
    ret = mqtt.publish(gauge, payload=json.dumps(message2), qos=0, retain=False)


def cellinfo2(data):
    infodata = data
    i = 0  # unpack into variables, ignore end of message byte '77'
    protect, vers, percent, fet, cells, sensors, temp1, temp2, b77 = struct.unpack_from('>HBBBBBHHB', infodata, i)
    temp1 = (temp1 - 2731) / 10
    temp2 = (temp2 - 2731) / 10  # fet 0011 = 3 both on ; 0010 = 2 disch on ; 0001 = 1 chrg on ; 0000 = 0 both off
    prt = (format(protect, "b").zfill(16))  # protect trigger (0,1)(off,on)
    message1 = {
        "meter": "bms",
        "ovp": int(prt[0:1]),  # overvoltage
        "uvp": int(prt[1:2]),  # undervoltage
        "bov": int(prt[2:3]),  # pack overvoltage
        "buv": int(prt[3:4]),  # pack undervoltage
        "cot": int(prt[4:5]),  # current over temp
        "cut": int(prt[5:6]),  # current under temp
        "dot": int(prt[6:7]),  # discharge over temp
        "dut": int(prt[7:8]),  # discharge under temp
        "coc": int(prt[8:9]),  # charge over current
        "duc": int(prt[9:10]),  # discharge under current
        "sc": int(prt[10:11]),  # short circuit
        "ic": int(prt[11:12]),  # ic failure
        "cnf": int(prt[12:13])  # config problem
    }
    ret = mqtt.publish(topic, payload=json.dumps(message1), qos=0, retain=False)
    message2 = {
        "meter": "bms",
        "protect": protect,
        "percent": percent,
        "fet": fet,
        "cells": cells,
        "temp1": temp1,
        "temp2": temp2
    }
    ret = mqtt.publish(topic, payload=json.dumps(message2), qos=0,
                       retain=False)  # not sending version number or number of temp sensors


def cellvolts1(data):  # process cell voltages
    global cells1
    celldata = data  # Unpack into variables, skipping header bytes 0-3
    i = 4
    cell1, cell2, cell3, cell4, cell5, cell6, cell7, cell8 = struct.unpack_from('>HHHHHHHH', celldata, i)
    cells1 = [cell1, cell2, cell3, cell4, cell5, cell6, cell7, cell8]  # needed for max, min, delta calculations
    message = {
        "meter": "bms",
        "cell1": cell1,
        "cell2": cell2,
        "cell3": cell3,
        "cell4": cell4,
        "cell5": cell5,
        "cell6": cell6,
        "cell7": cell7,
        "cell8": cell8
    }
    ret = mqtt.publish(gauge, payload=json.dumps(message), qos=0, retain=False)

    cellsmin = min(cells1)  # min, max, delta
    cellsmax = max(cells1)
    delta = cellsmax - cellsmin
    mincell = (cells1.index(min(cells1)) + 1)
    maxcell = (cells1.index(max(cells1)) + 1)
    message1 = {
        "meter": meter,
        "mincell": mincell,
        "cellsmin": cellsmin,
        "maxcell": maxcell,
        "cellsmax": cellsmax,
        "delta": delta
    }
    ret = mqtt.publish(gauge, payload=json.dumps(message1), qos=0, retain=False)


class SerialProtocol(asyncio.Protocol):

    def connection_made(self, transport):
        self.transport = transport
        print("Port opened.")
        asyncio.ensure_future(self.query_device())

    async def query_device(self):
        try:
            while True:
                byte_data_cmd03 = bytes.fromhex('DDA50300FFFD77')
                self.transport.serial.write(byte_data_cmd03)
                await asyncio.sleep(5)
                byte_data_cmd04 = bytes.fromhex('DDA50400FFFC77')
                self.transport.serial.write(byte_data_cmd04)
                await asyncio.sleep(5)
                await asyncio.sleep(z)

        except asyncio.CancelledError:
            print("Query loop interrupted.")
            self.transport.close()

    def data_received(self, data):
        print("Income data:", data)
        hex_data = binascii.hexlify(data)  # Given raw bytes, get an ASCII string representing the hex values
        text_string = hex_data.decode('utf-8')  # check incoming data for routing to decoding routines
        if text_string.find('dd04') != -1:  # x04 (1-8 cells)
            cellvolts1(data)
        elif text_string.find('dd03') != -1:  # x03
            cellinfo1(data)
        elif text_string.find('77') != -1 and len(text_string) == 28 or len(text_string) == 36:  # x03
            cellinfo2(data)


def connection_lost(self, exc):
    if exc:
        print("Error:", exc)
    else:
        print("Connection closed.")
    asyncio.get_event_loop().stop()


async def main():
    loop = asyncio.get_running_loop()
    atexit.register(disconnect)

    mqtt.connect(broker, port)

    transport, protocol = await serial_asyncio.create_serial_connection(
        loop, SerialProtocol, comport, baudrate=9600
    )

    try:
        await asyncio.Future()
    except KeyboardInterrupt:
        # Ctrl+C to quit
        print("User quit the program.")
        transport.close()

try:
    asyncio.run(main())
except KeyboardInterrupt:
    pass
