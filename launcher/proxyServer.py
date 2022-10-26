from calendar import c
from distutils.command.clean import clean
from pickletools import bytes8
import socket
import re
import uuid
import struct
from threading import Thread

# server's IP address
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 5002 # port we want to use

def send_msg(sock, msg, doPack):
    # For 1 byte (bool) send just that
    # Prefix each message with a 4-byte length (network byte order)
    if doPack and len(msg) > 1:
        msg = struct.pack('<I', len(msg)) + msg
    sock.sendall(msg)

def recv_msg(sock):
    # Read message length and unpack it into an integer
    raw_msglen = recvall(sock, 4)
    if not raw_msglen:
        return None
    msglen = struct.unpack('<I', raw_msglen)[0]
    # Read the message data
    return recvall(sock, msglen)

def recvall(sock, n):
    # Helper function to recv n bytes or return None if EOF is hit
    data = bytearray()
    while len(data) < n:
        packet = sock.recv(n - len(data))
        if not packet:
            return None
        data.extend(packet)
    return data

# initialize list/set of all connected client's sockets
client_sockets = dict()


class GameConnection:
    server: socket
    client: socket
    serverInit = False
    clientInit = False
    messageQueueIn = []
    messageQueueOut = []

    def __init__(self) -> None:
        self.server = None
        self.client = None
        pass

class Session:
    total = 1
    joined = 0
    password = ""
    protected = False
    name: str
    host: socket
    host_uuid: str
    players = []
    connections = []
    started = False

    def __init__(self, host: socket, name: str) -> None:
        self.name = name
        self.host = host
        self.players = [host]
        self.joined += 1

    def isJoined(self, player: socket) -> bool:
        return player in self.players

    def join(self, player: socket):
        if not self.isJoined(player) and self.joined < self.total:
            self.players.append(player)
            self.joined += 1

    def leave(self, player: socket):
        if not self.isJoined(player) or player == self.host:
            return

        self.players.remove(player)
        self.joined -= 1

    def addConnection(self, conn: socket, isServer: bool) -> GameConnection:
        #find uninitialized server connection
        for gc in self.connections:
            if isServer and not gc.serverInit:
                gc.server = conn
                gc.serverInit = True
                return gc
            if not isServer and not gc.clientInit:
                gc.client = conn
                gc.clientInit = True
                return gc
            
        #no existing connection - create the new one
        gc = GameConnection()
        if isServer:
            gc.server = conn
            gc.serverInit = True
        else:
            gc.client = conn
            gc.clientInit = True
        self.connections.append(gc)
        return gc

    def validPipe(self, conn) -> bool:
        for gc in self.connections:
            if gc.server == conn or gc.client == conn:
                return gc.serverInit and gc.clientInit
        return False

    def getPipe(self, conn) -> socket:
        for gc in self.connections:
            if gc.server == conn:
                return gc.client
            if gc.client == conn:
                return gc.server

     
# create a TCP socket
s = socket.socket()
# make the port as reusable port
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
# bind the socket to the address we specified
s.bind((SERVER_HOST, SERVER_PORT))
# listen for upcoming connections
s.listen(10)
print(f"[*] Listening as {SERVER_HOST}:{SERVER_PORT}")

# list of active sessions
sessions = dict()


def handleDisconnection(client: socket):
    sender = client_sockets[client]
    if sender["joined"]:
        if not sender["session"].started:
            if sender["session"].host == client:
                #destroy the session, sending messages inside the function
                deleteSession(sender["session"])
            else:
                sender["session"].leave(client)
                sender["joined"] = False
                message = f":>>KICK:{sender['session'].name}:{sender['username']}"
                for client_socket in sender["session"].players:
                    client_socket.send(message.encode())
            updateStatus(sender["session"])
            updateSessions()

    client.close()
    sender["valid"] = False


def send(client: socket, message: str):
    sender = client_sockets[client]
    if "valid" not in sender or sender["valid"]:
        client.send(message.encode())


def broadcast(clients: list, message: str):
    for c in clients:
        send(c, message)


def sendSessions(client: socket):
    msg2 = ""
    counter = 0
    for s in sessions.values():
        if not s.started:
            msg2 += f":{s.name}:{s.joined}:{s.total}:{s.protected}"
            counter += 1
    msg = f":>>SESSIONS:{counter}{msg2}"

    send(client, msg)


def updateSessions():
    for s in client_sockets.keys():
        sendSessions(s)


def deleteSession(session: Session):
    msg = f":>>KICK:{session.name}"
    for player in session.players:
        client_sockets[player]["joined"] = False
        msg2 = msg + f":{client_sockets[player]['username']}"
        send(player, msg2)
    
    sessions.pop(session.name)


def updateStatus(session: Session):
    msg = f":>>STATUS:{session.joined}"
    for player in session.players:
        msg += f":{client_sockets[player]['username']}:{client_sockets[player]['ready']}"
    broadcast(session.players, msg)


def startSession(session: Session):
    session.started = True
    session.host_uuid = str(uuid.uuid4())
    hostMessage = f":>>HOST:{session.host_uuid}:{session.joined - 1}" #one client will be connected locally
    for player in session.players:
        client_sockets[player]['uuid'] = str(uuid.uuid4())
        msg = f":>>START:{client_sockets[player]['uuid']}"
        send(player, msg)
    
    #host message must be after start message
    send(session.host, hostMessage)


def dispatch(client: socket, sender: dict, arr: bytes):
    
    if arr == None or len(arr) == 0:
        return

    #if len(sender["prevmessages"]):
    #    arr = sender["prevmessages"] + arr
    #    sender["prevmessages"] = bytes()

    #check for game mode connection
    msg = str(arr)
    if msg.find("Aiya!") != -1:
        sender["pipe"] = True

    if sender["pipe"]:
        if sender["game"]:
            sender["prevmessages"].append(arr)
        else:
            sender["prevmessages"].append(struct.pack('<I', len(arr)) + arr)
            match = re.search(r"\((\w+)\)", msg)
            _appType = ''
            if match != None:
                _appType = match.group(1)
                sender["apptype"] = _appType
            
            _uuid = arr.decode()
            if not _uuid == '' and not sender["apptype"] == '':
                #search for uuid
                for session in sessions.values():
                    if session.started:
                        if _uuid.find(session.host_uuid) != -1 and sender["apptype"] == "server":
                            gc = session.addConnection(client, True)
                            #send_msg(gc.server, gc.messageQueueIn)
                            #gc.messageQueueIn = bytes()
                            sender["session"] = session
                            sender["game"] = True
                            #read boolean flag for the endian
                            sender["prevmessages"].append(client.recv(1))
                            #if not gc.clientInit:
                            #    gc.messageQueueOut += arr
                            return

                        if sender["apptype"] == "client":
                            for p in session.players:
                                if _uuid.find(client_sockets[p]["uuid"]) != -1:
                                    #client connection
                                    gc = session.addConnection(client, False)
                                    #send_msg(gc.client, gc.messageQueueOut)
                                    #gc.messageQueueOut = bytes()
                                    sender["session"] = session
                                    sender["game"] = True
                                    #read boolean flag for the endian
                                    sender["prevmessages"].append(client.recv(1))
                                    #if not gc.serverInit:
                                    #    gc.messageQueueIn += arr
                                    #    return
                                    break

    #game mode
    if sender["pipe"] and sender["game"] and sender["session"].validPipe(client):
        #send messages from queue
        opposite = sender["session"].getPipe(client)
        for x in client_sockets[opposite]["prevmessages"]:
            client.sendall(x)
        client_sockets[opposite]["prevmessages"].clear()

        try:
            for x in sender["prevmessages"]:
                opposite.sendall(x)
        except Exception as e:
            print(f"[!] Error: {e}")
        sender["prevmessages"].clear()
        return

    if sender["pipe"]:
        return
    
    #lobby mode
    msg = arr.decode()
    _open = msg.partition('<')
    _close = _open[2].partition('>')
    if _open[0] != '' or _open[1] == '' or _open[2] == '' or _close[0] == '' or _close[1] == '':
        print(f"[!] Incorrect message from {sender['address']}: {msg}")
        return

    _nextTag = _close[2].partition('<')
    tag = _close[0]
    tag_value = _nextTag[0]

    #greetings to the server
    if tag == "GREETINGS":
        if sender["auth"]:
            print(f"[!] Greetings from authorized user {sender['username']} {sender['address']}")
            return
        print(f"[*] User {sender['address']} autorized as {tag_value}")
        sender["username"] = tag_value
        sender["auth"] = True
        sender["joined"] = False
        sendSessions(client)

    #VCMI version received
    if tag == "VER" and sender["auth"]:
        print(f"[*] User {sender['username']} has version {tag_value}")

    #message received
    if tag == "MSG" and sender["auth"]:
        message = f":>>MSG:{sender['username']}:{tag_value}"
        if sender["joined"]:
            broadcast(sender["session"].players, message)
        else:
            broadcast(client_sockets.keys(), message)

    #new session
    if tag == "NEW" and sender["auth"] and not sender["joined"]:
        if tag_value in sessions:
            #refuse creating game
            message = f":>>ERROR:Cannot create session with name {tag_value}, session with this name already exists"
            send(client, message)
            return
        
        sessions[tag_value] = Session(client, tag_value)
        sender["joined"] = True
        sender["ready"] = False
        sender["session"] = sessions[tag_value]

    #set password for the session
    if tag == "PSWD" and sender["auth"] and sender["joined"] and sender["session"].host == client:
        sender["session"].password = tag_value
        sender["session"].protected = tag_value != ""

    #set amount of players to the new session
    if tag == "COUNT" and sender["auth"] and sender["joined"] and sender["session"].host == client:
        if sender["session"].total != 1:
            #refuse changing amount of players
            message = f":>>ERROR:Changing amount of players is not possible for existing session"
            send(client, message)
            return

        sender["session"].total = int(tag_value)
        #now session is ready to be broadcasted
        message = f":>>JOIN:{sender['session'].name}:{sender['username']}"
        send(client, message)
        updateStatus(sender["session"])
        updateSessions()

    #join session
    if tag == "JOIN" and sender["auth"] and not sender["joined"]:
        if tag_value not in sessions:
            message = f":>>ERROR:Session with name {tag_value} doesn't exist"
            send(client, message)
            return
        
        if sessions[tag_value].joined >= sessions[tag_value].total:
            message = f":>>ERROR:Session {tag_value} is full"
            send(client, message)
            return

        if sessions[tag_value].started:
            message = f":>>ERROR:Session {tag_value} is started"
            send(client, message)
            return

        sender["joined"] = True
        sender["ready"] = False
        sender["session"] = sessions[tag_value]

    if tag == "PSWD" and sender["auth"] and sender["joined"] and sender["session"].host != client:
        if not sender["session"].protected or sender["session"].password == tag_value:
            sender["session"].join(client)
            message = f":>>JOIN:{sender['session'].name}:{sender['username']}"
            broadcast(sender["session"].players, message)
            updateStatus(sender["session"])
            updateSessions()

        else:
            sender["joined"] = False
            message = f":>>ERROR:Incorrect password"
            send(client, message)
            return

    #leaving session
    if tag == "LEAVE" and sender["auth"] and sender["joined"] and sender["session"].name == tag_value:
        if sender["session"].host == client:
            #destroy the session, sending messages inside the function
            deleteSession(sender["session"])
        else:
            message = f":>>KICK:{sender['session'].name}:{sender['username']}"
            broadcast(sender["session"].players, message)
            sender["session"].leave(client)
            sender["joined"] = False
            updateStatus(sender["session"])
        updateSessions() 

    if tag == "READY" and sender["auth"] and sender["joined"] and sender["session"].name == tag_value:
        if sender["session"].joined > 0 and sender["session"].host == client:
            startSession(sender["session"])
            updateSessions()

    dispatch(client, sender, (_nextTag[1] + _nextTag[2]).encode())


def listen_for_client(cs):
    """
    This function keep listening for a message from `cs` socket
    Whenever a message is received, broadcast it to all other connected clients
    """
    while True:
        try:
            # keep listening for a message from `cs` socket
            if client_sockets[cs]["game"]:
                msg = cs.recv(4096)
            else:
                msg = recv_msg(cs)
            #msg = cs.recv(2048)
        except Exception as e:
            # client no longer connected
            print(f"[!] Error: {e}")
            handleDisconnection(cs)            
            return
            
        dispatch(cs, client_sockets[cs], msg)


while True:
    # we keep listening for new connections all the time
    client_socket, client_address = s.accept()
    print(f"[+] {client_address} connected.")
    # add the new connected client to connected sockets
    client_sockets[client_socket] = {"address": client_address, "auth": False, "username": "", "joined": False, "game": False, "pipe": False, "apptype": "", "prevmessages": []}
    # start a new thread that listens for each client's messages
    t = Thread(target=listen_for_client, args=(client_socket,))
    # make the thread daemon so it ends whenever the main thread ends
    t.daemon = True
    # start the thread
    t.start()

 # close client sockets
for cs in client_sockets:
    cs.close()
# close server socket
s.close()