from tkinter import messagebox, Button, Label, W, E, N, S
from PIL import Image, ImageTk
import socket, threading, sys, traceback, os
import time

from RtpPacket import RtpPacket

CACHE_FILE_NAME = "cache-"
CACHE_FILE_EXT = ".jpg"


class Client:
    INIT = 0
    READY = 1
    PLAYING = 2
    state = INIT

    SETUP = 0
    PLAY = 1
    PAUSE = 2
    TEARDOWN = 3

    # Initiation..
    def __init__(self, master, serveraddr, serverport, rtpport, filename, has3Button):
        self.master = master
        self.master.protocol("WM_DELETE_WINDOW", self.handler)
        if(has3Button):
            self.create3ButtonInterface()
        else:
            self.createWidgets()
        self.serverAddr = serveraddr
        self.serverPort = int(serverport)
        self.rtpPort = int(rtpport)
        self.fileName = filename
        self.rtspSeq = 0
        self.sessionId = 0
        self.requestSent = -1
        self.teardownAcked = 0
        self.connectToServer()
        if(has3Button):
            self.setupMovie()
        self.frameNbr = 0

        # statistical data
        self.timerBegin = 0
        self.timerEnd = 0
        self.timer = 0
        self.bytes = 0
        self.packets = 0
        self.packetsLost = 0
        self.lastSequence = 0
        self.totalJitter = 0
        self.arrivalTimeofPreviousPacket = 0
        self.lastPacketSpacing = 0

    def createWidgets(self):
        """Build GUI."""
        # Create Setup button
        self.setup = Button(self.master, width=20, padx=3, pady=3)
        self.setup["text"] = "Setup"
        self.setup["command"] = self.setupMovie
        self.setup.grid(row=1, column=0, padx=2, pady=2)

        # Create Play button
        self.start = Button(self.master, width=20, padx=3, pady=3)
        self.start["text"] = "Play"
        self.start["command"] = self.playMovie
        self.start.grid(row=1, column=1, padx=2, pady=2)

        # Create Pause button
        self.pause = Button(self.master, width=20, padx=3, pady=3)
        self.pause["text"] = "Pause"
        self.pause["command"] = self.pauseMovie
        self.pause.grid(row=1, column=2, padx=2, pady=2)

        # Create Teardown button
        self.teardown = Button(self.master, width=20, padx=3, pady=3)
        self.teardown["text"] = "Teardown"
        self.teardown["command"] = self.exitClient
        self.teardown.grid(row=1, column=3, padx=2, pady=2)

        # Create a label to display the movie
        self.label = Label(self.master, height=19)
        self.label.grid(row=0, column=0, columnspan=4, sticky=W + E + N + S, padx=5, pady=5)

    def create3ButtonInterface(self):
        # Create Play button
        self.start = Button(self.master, width=20, padx=3, pady=3)
        self.start["text"] = "Play"
        self.start["command"] = self.playMovie
        self.start.grid(row=1, column=1, padx=2, pady=2)

        # Create Pause button
        self.pause = Button(self.master, width=20, padx=3, pady=3)
        self.pause["text"] = "Pause"
        self.pause["command"] = self.pauseMovie
        self.pause.grid(row=1, column=2, padx=2, pady=2)

        # Create Stop button
        self.teardown = Button(self.master, width=20, padx=3, pady=3)
        self.teardown["text"] = "Stop"
        self.teardown["command"] = self.exitClient
        self.teardown.grid(row=1, column=3, padx=2, pady=2)

        # Create a label to display the movie
        self.label = Label(self.master, height=19)
        self.label.grid(row=0, column=0, columnspan=4, sticky=W + E + N + S, padx=5, pady=5)

    def setupMovie(self):
        """Setup button handler."""
        if self.state == self.INIT:
            self.sendRtspRequest(self.SETUP)

    def exitClient(self):
        """Teardown button handler."""
        self.sendRtspRequest(self.TEARDOWN)
        self.master.destroy()  # Close the gui window
        os.remove(CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT)  # Delete the cache image from video

    def pauseMovie(self):
        """Pause button handler."""
        if self.state == self.PLAYING:
            self.sendRtspRequest(self.PAUSE)

    def playMovie(self):
        """Play button handler."""
        if self.state == self.READY:
            # Create a new thread to listen for RTP packets
            threading.Thread(target=self.listenRtp).start()
            self.playEvent = threading.Event()
            self.playEvent.clear()
            self.sendRtspRequest(self.PLAY)

    def listenRtp(self):
        """Listen for RTP packets."""
        while True:
            try:
                data = self.rtpSocket.recv(20480)
                if data:
                    rtpPacket = RtpPacket()
                    rtpPacket.decode(data)

                    currFrameNbr = rtpPacket.seqNum()
                    arrivalTimeOfPacket = time.perf_counter()
                    print("Current Seq Num: " + str(currFrameNbr))

                    self.bytes += len(rtpPacket.getPacket())

                    if currFrameNbr > self.frameNbr:  # Discard the late packet
                        self.frameNbr = currFrameNbr
                        self.updateMovie(self.writeFrame(rtpPacket.getPayload()))
                        #statUpdate
                        self.packets += 1
                        self.packetsLost += currFrameNbr - self.lastSequence - 1
                        #calculate total jitter
                        if self.lastSequence == currFrameNbr - 1 and currFrameNbr > 1:
                            interPacketSpacing = arrivalTimeOfPacket - self.arrivalTimeofPreviousPacket
                            jitterIncrement = abs(interPacketSpacing-self.lastPacketSpacing)
                            self.totalJitter = self.totalJitter + jitterIncrement
                            self.lastPacketSpacing = interPacketSpacing

                        self.arrivalTimeofPreviousPacket = arrivalTimeOfPacket
                        self.lastSequence = currFrameNbr
            except:
                # Stop listening upon requesting PAUSE or TEARDOWN
                if self.playEvent.isSet():
                    self.displayStats()
                    break

                # Upon receiving ACK for TEARDOWN request,
                # close the RTP socket
                if self.teardownAcked == 1:
                    self.displayStats()
                    self.rtpSocket.shutdown(socket.SHUT_RDWR)
                    self.rtpSocket.close()
                    break

    def writeFrame(self, data):
        """Write the received frame to a temp image file. Return the image file."""
        cachename = CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT
        file = open(cachename, "wb")
        file.write(data)
        file.close()

        return cachename

    def updateMovie(self, imageFile):
        """Update the image file as video frame in the GUI."""
        photo = ImageTk.PhotoImage(Image.open(imageFile))
        self.label.configure(image=photo, height=288)
        self.label.image = photo

    def connectToServer(self):
        """Connect to the Server. Start a new RTSP/TCP session."""
        self.rtspSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.rtspSocket.connect((self.serverAddr, self.serverPort))
        except:
            messagebox.showwarning('Connection Failed', 'Connection to \'%s\' failed.' % self.serverAddr)

    def sendRtspRequest(self, requestCode):
        """Send RTSP request to the server."""

        # Setup request
        if requestCode == self.SETUP and self.state == self.INIT:
            threading.Thread(target=self.recvRtspReply).start()
            # Update RTSP sequence number.
            self.rtspSeq += 1

            # Write the RTSP request to be sent.
            request = "SETUP %s RTSP/1.0" % self.fileName
            request += "\nCSeq: %d" % self.rtspSeq
            request += "\nTransport: RTP/UDP; client_port= %d" % self.rtpPort

            # Keep track of the sent request.
            self.requestSent = self.SETUP

        # Play request
        elif requestCode == self.PLAY and self.state == self.READY:
            # Update RTSP sequence number.
            self.rtspSeq += 1

            # Write the RTSP request to be sent.
            request = "PLAY %s RTSP/1.0" % self.fileName
            request += "\nCSeq: %d" % self.rtspSeq
            request += "\nSession: %d" % self.sessionId

            # Keep track of the sent request.
            self.requestSent = self.PLAY
        # Pause request
        elif requestCode == self.PAUSE and self.state == self.PLAYING:
            # Update RTSP sequence number.
            self.rtspSeq += 1

            # Write the RTSP request to be sent.
            request = "PAUSE %s RTSP/1.0" % self.fileName
            request += "\nCSeq: %d" % self.rtspSeq
            request += "\nSession: %d" % self.sessionId

            # Keep track of the sent request.
            self.requestSent = self.PAUSE

        # Teardown request
        elif requestCode == self.TEARDOWN and not self.state == self.INIT:
            # Update RTSP sequence number.
            self.rtspSeq += 1

            # Write the RTSP request to be sent.
            request = "TEARDOWN %s RTSP/1.0" % self.fileName
            request += "\nCSeq: %d" % self.rtspSeq
            request += "\nSession: %d" % self.sessionId

            # Keep track of the sent request.
            self.requestSent = self.TEARDOWN
        else:
            return

        # Send the RTSP request using rtspSocket.
        self.rtspSocket.sendall(request.encode('utf-8'))

        print('\nData sent:\n' + request)

    def recvRtspReply(self):
        """Receive RTSP reply from the server."""
        while True:
            reply = self.rtspSocket.recv(1024)

            if reply:
                self.parseRtspReply(reply)

            # Close the RTSP socket upon requesting Teardown
            if self.requestSent == self.TEARDOWN:
                self.rtspSocket.shutdown(socket.SHUT_RDWR)
                self.rtspSocket.close()
                break

    def parseRtspReply(self, data):
        """Parse the RTSP reply from the server."""
        decodedData = data.decode()  # ^^^^^^
        lines = decodedData.split('\n')
        seqNum = int(lines[1].split(' ')[1])

        # Process only if the server reply's sequence number is the same as the request's
        if seqNum == self.rtspSeq:
            session = int(lines[2].split(' ')[1])
            # New RTSP session ID
            if self.sessionId == 0:
                self.sessionId = session

            # Process only if the session ID is the same
            if self.sessionId == session:
                if int(lines[0].split(' ')[1]) == 200:
                    if self.requestSent == self.SETUP:

                        # Update RTSP state.
                        self.state = self.READY

                        # Open RTP port.
                        self.openRtpPort()
                    elif self.requestSent == self.PLAY:
                        self.state = self.PLAYING


                        # start timer if not already playing
                        if self.timerBegin == 0:
                            self.timerBegin = time.perf_counter()
                            self.arrivalTimeofPreviousPacket = time.perf_counter()

                    elif self.requestSent == self.PAUSE:
                        self.state = self.READY
                        # set timer when paused and playing previously
                        if self.timerBegin > 0:
                            self.timerEnd = time.perf_counter()
                            self.timer += self.timerEnd - self.timerBegin
                            self.timerBegin = 0

                        # The play thread exits. A new thread is created on resume.
                        self.playEvent.set()
                    elif self.requestSent == self.TEARDOWN:
                        self.state = self.INIT
                        self.timerEnd = time.perf_counter()

                        # end timer
                        self.timer += self.timerEnd - self.timerBegin

                        # Flag the teardownAcked to close the socket.
                        self.teardownAcked = 1

    def openRtpPort(self):
        """Open RTP socket binded to a specified port."""
        # Create a new datagram socket to receive RTP packets from the server
        self.rtpSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # DGRAM for UDP
        # Set the timeout value of the socket to 0.5sec
        self.rtpSocket.settimeout(.5)

        try:
            # Bind the socket to the address using the RTP port given by the client user
            self.state = self.READY
            self.rtpSocket.bind(("", self.rtpPort))
        except:
            messagebox.showwarning('Unable to Bind', 'Unable to bind PORT=%d' % self.rtpPort)

    def handler(self):
        """Handler on explicitly closing the GUI window."""
        self.pauseMovie()
        if messagebox.askokcancel("Quit?", "Are you sure you want to quit?"):
            self.exitClient()
        else:  # When the user presses cancel, resume playing.
            self.playMovie()

    def displayStats(self):
        """Displays observed statistics"""
        totalPackets = ((self.packetsLost) / ((self.packetsLost) + self.packets)) * 100
        print("Packets Received: %d packets" % self.packets)
        print("Packets Lost: %d packets" % (self.packetsLost))
        print("Packet Loss Rate: %d%%" % totalPackets)
        print("Play time: %.2f seconds" % self.timer)
        print("Bytes received: %d bytes" % self.bytes)
        print("Video Data Rate: %d bytes per second" % (self.bytes / self.timer))
        print("Total Jitter: %.3fms" % (self.totalJitter * 1000))
        print("Average Jitter: %.3fms" % ((self.totalJitter / self.packets) * 1000))

