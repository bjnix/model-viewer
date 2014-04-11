# Copyright (c) 2001, Stanford University
# All rights reserved.
#
# See the file LICENSE.txt for information on redistributing this software.

"""Chromium mothership functions

These functions are used in configuration files to direct the mothership.
Use these functions to describe your SPU network.  The SPU network is a DAG
of nodes.  Each node contains a SPU chain.  At the top of the graph is an
CRApplicationNode.  It connects to one or more CRNetworkNodes.

Public functions and classes:
    CR:             Main class that controls the mothership
    SPU:            Class that defines a Stream Processing Unit.
    CRNetworkNode:  Sub class of CRNode that defines a node in the SPU
                    graph that handles incoming and outgoing network
                    traffic.
    CRApplicationNode:
                    Sub class of CRNode that defines the start of the the
                    SPU graph.
    CRAddStartupCallback:
                    Add a callback to be called on cr.Go()

Other internal functions/classes:
    CRNode:         Base class that defines a node in the SPU graph
    CRDebug:        Used to print out debugging messages.
    CROutput:       Used to print messages to a logfile.
    Fatal:          Used to print out a debugging messages and exit.
    MakeString:     Converts a Python object to a string.
    SockWrapper:    Internal convenience class for handling sockets
"""

import sys, string, types, traceback, re, threading, os, socket, select, signal, pickle, copy, time

from crconfig import arch, crdir, crbindir, crlibdir

# Version string
Version = "1.9"

# Default port we'll listen on (also set in cr_net.h)
DefaultMothershipPort = 10000

# This controls whether info/debug messages are printed
# (0=none, 1=info, 2=info+debug)
DebugLevel = 1

# It seems these aren't defined in all versions of Python
True = 1
False = 0

# Some help in figuring out the domains of some non-qualified hostnames.
# See QualifyHostname() below.
HostPrefixPairs = [
	('iam','psc.edu'),
	('tg-v','uc.teragrid.org'),
	('tile-0-0','ibnet'),
	('tile-0-1','ibnet'),
	('tile-0-2','ibnet'),
	('tile-0-3','ibnet'),
	('tile-0-4','ibnet'),
	('tile-0-5','ibnet'),
	('tile-0-6','ibnet'),
	('tile-0-7','ibnet')
]

def CRSetDebugLevel(level):
	global DebugLevel
	DebugLevel = level

def CRInfo( str ):
	"""CRInfo(str)
	Prints informational messages to stderr."""
	global DebugLevel
	if DebugLevel >= 1:
		print >> sys.stderr, str

def CRDebug( str ):
	"""CRDebug(str)
	Prints debugging message to stderr."""
	global DebugLevel
	if DebugLevel >= 2:
		print >> sys.stderr, str

def CROutput( str ):
	"""CROutput(str)
	Prints message to logfile."""
	filename = os.environ.get("CR_PERF_MOTHERSHIP_LOGFILE")
	if filename:
		f = open(filename, "a")
		if f:
			f.write("%s\n" % str )
			f.close()
		else:
			CRDebug("Unable to open performance monitoring log file %s\n" % file)
	else:
		CRDebug("NO Performance Logfile set, check CR_PERF_MOTHERSHIP_LOGFILE")

def CRAddStartupCallback( cb ):
	"""CRAddStartupCallback( cb )
	Causes cb(thisCR) to be called from thisCR.Go()."""
	CR.startupCallbacks.append(cb)

allSPUs = {}

def Fatal( str ):
	"""Fatal(str)
	Prints debugging message to stderr and skeddadles."""
	print >> sys.stderr, str
	sys.exit(-1)

def MakeString( x ):
	"""MakeString(x)
	Converts an object to a string"""
	if type(x) == types.StringType:
		return x
	else:
		return repr(x)

def SameHost( host1, host2 ):
	"""Return 1 if host1 and host2 name the same host.	Return 0 otherwise.
	For example, if host1='foo' and host2='foo.bar.com' we'll return 1.
	"""
	try:
		if host1 == host2 or socket.gethostbyname(host1) == socket.gethostbyname(host2):
			return 1
		else:
			return 0
	except socket.gaierror:
		if string.split(host1,".")[0] == string.split(host2,".")[0]:
			return 1
		else:
			return 0


# Constraint tests.  These are used to match hosts, either statically
# or dynamically.  Each test must define whether it is appropriate
# for use with static host matching or dynamic host matching.
STATIC_CONSTRAINT = True
DYNAMIC_CONSTRAINT = False

ConstraintTests = { }

def NameConstraint(testName, matchName):
	return SameHost(string.lower(testName), string.lower(matchName))
ConstraintTests["name"] = (NameConstraint, STATIC_CONSTRAINT)

def DynamicConstraint(testName, dummy):
	return 1
ConstraintTests["dynamic"] = (DynamicConstraint, DYNAMIC_CONSTRAINT)

def RegexConstraint(testName, pattern):
	return re.search(pattern, testName)
ConstraintTests["regex"] = (RegexConstraint, DYNAMIC_CONSTRAINT)

def RegexFullConstraint(testName, pattern):
	fullName = QualifyHostname(testName)
	return re.search(pattern, fullName)
ConstraintTests["regex_full"] = (RegexFullConstraint, DYNAMIC_CONSTRAINT)

def PatternConstraint(testName, compiledPattern):
	return compiledPattern.search(testName)
ConstraintTests["pattern"] = (PatternConstraint, DYNAMIC_CONSTRAINT)

def PatternFullConstraint(testName, compiledPattern):
	fullName = QualifyHostname(testName)
	return compiledPattern.search(fullName)
ConstraintTests["pattern_full"] = (PatternFullConstraint, DYNAMIC_CONSTRAINT)

def MatchDynamicConstraints(node, hostToMatch):
	for (constraintName, constraintArg) in node.constraints:
		(testFunction, constraintType) = ConstraintTests[constraintName]
		if not testFunction(hostToMatch, constraintArg):
			return 0
	return 1

def MatchStaticConstraints(node, hostToMatch):
	for (constraintName, constraintArg) in node.constraints:
		(testFunction, constraintType) = ConstraintTests[constraintName]
		if constraintType != STATIC_CONSTRAINT or not testFunction(hostToMatch, constraintArg):
			return 0
	return 1
	

# This structure will contain a list of all dynamic host indicators
# found during definition; they will be assigned as servers come in
# through the MatchNode() routine (following).
dynamicHosts = { }

# This structure will contain an entry for every dynamic host
# indicator that has not yet been resolved.  It is used to
# know when the main application (which needs a list of all
# servers) may continue.
dynamicHostsNeeded = { }


def MatchStaticNode(node, hostToMatch):
	return MatchStaticConstraints(node, hostToMatch)

def MatchResolvedNode(node, hostToMatch):
	if dynamicHosts.has_key(node.host):
		return SameHost(string.lower(dynamicHosts[node.host]), string.lower(hostToMatch))
	else:
		return 0

# Only the "grandmothership" may resolve nodes.
def ResolveNode(node, hostToMatch):
	dynamicHosts[node.host] = hostToMatch
	try:
		del dynamicHostsNeeded[node.host]
	except:
		pass

def MatchUnresolvedNode(node, hostToMatch):
	if MatchDynamicConstraints(node, hostToMatch):
		ResolveNode(node, hostToMatch)
		return 1
	else:
		return 0
		
def QualifyHostname( host ):
	"""Converts host to a fully qualified domain name.
	Basicially, look if 'host' contains a dot.  If not, search the
	HostPrefixPairs list to find a suitable domain to append onto
	the hostname."""
	host= re.sub("\.local$", "", host)
#	print "pre-qualified: "+host

	if string.find(host, '.') >= 0:
		# OK as-is
		return host
	else:
		# try to find a matching prefix in HostPrefixPairs list
		for (prefix, domain) in HostPrefixPairs:
			if string.find(host, prefix) == 0:
#				print "post-qualified: "+"ib-" + host + '.' + domain
				return "ib-" + host + '.' + domain
		# Look if the CR_DEFAULT_DOMAIN env var is set
		domain = os.environ.get("CR_DEFAULT_DOMAIN")
		if domain:
			print "default domain!"
			return host + '.' + domain
		# finally, try using the socket.getfqdn() function
		return socket.getfqdn(host)

class SPU:
	"""Main class that defines a Stream Processing Unit.

	public functions:

	    Conf:	Sets a key/value list in this SPU's configuration
	    AddServer:  Tells a client node where to find its server.
		AddDisplay: Adds a 'display' to the list of displays (for tilesort)
		TileLayoutFunction:  Registers a function to call when this SPU is
                             asked for a new tile layout.
	"""

	def __init__( self, name ):
		"""Creates a SPU with the given name."""
		self.name = name
		self.config = {}
		self.clientargs = []
		self.servers = []
		self.layoutFunction = None
		self.displays = []


	def Conf( self, key, *values ):
		"""Set a SPU configuration option."""
		# XXX we'll eventually force values to be a single value or a list!
		if type(values) == types.TupleType and len(values) > 1:
			print "***WARNING: Obsolete syntax detected in Conf('%s', ...)!" % key
			print "***WARNING: Put brackets around N-element values (i.e. Python list syntax)."
		if len(values) > 1:
			self.config[key] = list(values)
		else:
			self.config[key] = values[0]

	def __add_server( self, node, formatURL ):
		self.servers.append( (node, formatURL) )

	def AddServer( self, node, protocol='tcpip', port=7000 ):
		"""AddServer(node, protocol='tcpip', port=7000)
		Associates a server with an SPU and tells it how to connect to it.
		The SPU will typically be a pack SPU or tilesort SPU.
		"""
		if (protocol.startswith('file') or protocol.startswith('swapfile')):
			self.__add_server( node, "%s" % protocol )
			# Don't tell the server "node" about this.
		else:
			# XXX use node.host or node.ipaddr here??? (BP)
			# Note that this is a format that will be later converted;
			# if there's a dynamic host reference, we cannot convert it now.
			self.__add_server( node, "%s://%%(host)s:%d" % (protocol, port) )
			#self.__add_server( node, "%s://ivs.ibnet:%d" % (protocol, port) )	
			# use this for tcp/ip : send hostname rather than ip
			# (waiting for getaddrinfo, for probing which one is
			#  available)
		if node != None:
			node.Conf( 'port', port )
			node.AddClient( self, protocol )

	def AddDisplay(self, display_id, w, h, align_matrix, align_matrix_inv):
		"""AddDisplay(display_id, w, h, align_matrix, align_matrix_inv)
		Adds a display with a given id and size to spu, for the 
		tilesort SPU"""
		self.displays.append( (display_id, w, h, align_matrix, align_matrix_inv) )

	def TileLayoutFunction( self, layoutFunc ):
		"""Set the tile layout callback function for a tilesort SPU."""
		# Set the tile layout function for a tilesort SPU
		assert self.name == "tilesort"
		self.layoutFunction = layoutFunc


class CRNode:
	"""Base class that defines a node in the SPU graph

	public functions:

	    Rank:   Sets the node's rank.
	    AddSPU:	Adds a SPU to the front of the SPU chain.
	    SPUDir:	Sets the directory SPUs start in.
	    AutoStart:	Pass this method a string to start the process
	    		associated with this CRNode from the mothership.
			You can pass a list of strings as the argument
			for use in os.spawnv() or a single string which
			will be split into a list. Make sure the first
			thing you pass is the full path to the executable.

			Examples:
			CRNode dummy( 'jimbobsbox' )
			dummy.AutoStart( "/usr/bin/ssh jimbobsbox crserver" )

			CRNode dummy( 'matilda' )
			dummy.AutoStart( ["/usr/bin/ssh", "matilda", "setenv FILE /Poorly Named/Data.1 ; crserver "] )
	"""
	SPUIndex = 0

	def __init__( self, host, constraint = "name", constraintArg = None ):
		"""CRNode(host)
		Creates a node on the given "host"."""
		if (host == 'localhost'):
			host = socket.getfqdn()
		self.host = host
		self.SPUs = []
		self.spokenfor = 0
		self.spusloaded = 0
		self.config = {}
		self.alias = host
		self.autostart = ""
		self.autostart_argv = []
		self.dynamic_host = False
		self.nodeIndex = -1 # set when added to a CR
		self.crut_spokenfor = 0

		# Add the default constraint to the node.
		self.constraints = []
		if constraintArg == None:
			constraintArg = self.host
		self.AddConstraint(constraint, constraintArg)

	def Alias( self, name ):
		self.alias = name
	
	def Rank( self, rank ):
		"""Rank(rank)
		Sets the node's rank."""
		self.config['rank'] = str( rank )

	def AddSPU( self, spu ):
		"""AddSPU(spu)
		Adds the given SPU to the tail of the SPU chain."""
		self.SPUs.append( spu )
		spu.ID = CRNode.SPUIndex
		spu.node = self
		CRNode.SPUIndex += 1
		allSPUs[spu.ID] = spu

	def Conf( self, key, value ):
		"""Sets a key/value list in this node's configuration"""
		self.config[key] = value

	def SPUDir( self, dir ):
		"""SPUDir(dir)
		Sets the directory that SPUs start in."""
		self.Conf('spu_dir', dir)

	def AutoStart( self, program ):
		if type( program ) == types.StringType:
			self.autostart_argv = string.split( program )
			self.autostart = self.autostart_argv[0]
		else:
			self.autostart_argv = program
			self.autostart = program[0]

	def SetPosition(self, x, y):
		# not used by mothership, set by graphical config tool
		pass

	def AddConstraint(self, constraint, arg = None):
		# Make sure it's a valid constraint
		try:
			(testFunction, constraintType) = ConstraintTests[constraint]
		except:
			print "***WARNING: unknown constraint '%s' on host '%s' ignored" % (constraint, self.host)
			return
		if constraintType != STATIC_CONSTRAINT:
			self.dynamic_host = True
			dynamicHostsNeeded[self.host] = 1
		self.constraints.append( (constraint, arg) )

	def GetClients( self ):
		"""Return list of (spu, protocol) tuples who are clients of
		this node.  The CRNetworkNode class will override this."""
		return None


class CRNetworkNode(CRNode):
	"""Sub class of CRNode that defines a node in the SPU graph that
	handles incoming and outgoing network traffic.

	public functions:

	    Conf:	Sets a key/value list in this node's configuration
	    AddClient:	Adds a SPU to the list of clients.
		FileClient: Add a file-readback client
	    AddTile:	Adds a tile to the list of tiles
		AddTileToDisplay: Adds a tile to a specified collection of tiles (a display)

	"""
	def __init__( self, host='localhost', constraint = "name", constraintArg = None ):
		"""CRNetworkNode(host='localhost')
		Creates a network node for the given "host"."""
		CRNode.__init__(self,host,constraint,constraintArg)
		self.clients = []         # list of SPUs
		self.file_clients = []    # list of "file://name" URLs
		self.tiles = []           # list of (x,y,w,h) tuples
		self.tiles_on_displays = []

	def AddClient( self, spu, protocol ):
		"""AddClient(spu, protocol)
		Adds a spu, communicating with "protocol", to the list of clients."""
		self.clients.append( (spu, protocol) )

	def GetClients( self ):
		"""Return list of (spu, protocol) tuples who are clients of
		this node."""
		return self.clients

	def FileClient( self, fname ):
		"""FileClient(node, fname)
		Adds a file-readback client link from the named file."""
		self.file_clients.append( "file://%s" % fname )

	def AddTile( self, x, y, w, h ):
		"""AddTile(x, y, w, h)
		Defines a tile with the given geometry to be used by a
		tilesort SPU.
		"""
		self.tiles.append( (x,y,w,h) )

	def AddTileToDisplay( self, display_id, x, y, w, h ):
		"""AddTileToDisplay(display_id, x, y, w, h)
		Similar to AddTile, but for use with specifing displays.
		Note that (x, y) are relative to the origin of the 
		display, not the mural!
		"""
		self.tiles_on_displays.append( (display_id,x,y,w,h) )


class CRVNCServerNode(CRNode):
	"""This class is used for VNC/Replicate SPU configurations.
	The config file should create one of these - it'll be shared by all
	vncviewers that might be run."""
	def __init__(self):
		"""Create a new CR VNC Server node."""
		CRNode.__init__(self, host="anyhost")


class CRUTServerNode(CRNode):
	"""Sub class of CRNode that defines a node in the SPU graph that
	handles outgoing network traffic for events.

	public functions:

	    Conf:	Sets a key/value list in this node's configuration
	    AddCRUTClient:	Adds a client to the list of crutclients.
	"""

	def __init__( self, host='localhost', constraint = "name", constraintArg = None ):
		"""CRUTServerNode(host='localhost')
		Creates a network node for the given "host"."""
		CRNode.__init__(self,host,constraint,constraintArg)
		self.crutclients = []

	#A crutserver will be creating events, it should be the only server
	def __add_crut_client( self, node, url ):
		self.crutclients.append( (node, url) )

	def AddCRUTClient( self, node, protocol='tcpip', port=9000 ):
		"""AddCRUTClient(node, protocol='tcpip', port=9000)
		Tells a crutserver node where to find a client."""
		self.__add_crut_client( node, "%s://%s:%d" % (protocol,node.host,port) )
		

class CRUTProxyNode(CRNode):
	"""Sub class of CRNode that defines a node in the SPU graph that
	handles incoming and outgoing network traffic for events.

	public functions:

	    Conf:	Sets a key/value list in this node's configuration
	    AddCRUTClient:	Adds a client to the list of clients.
	"""

	def __init__( self, host='localhost', constraint = "name", constraintArg = None ):
		"""CRUTProxyNode(host='localhost')
		Creates a network node for the given "host"."""
		CRNode.__init__(self,host,constraint,constraintArg)
		self.crutclients = []
		self.crutservers = []

	def __add_crut_client( self, node, url ):
		self.crutclients.append( (node, url) )

	def AddCRUTClient( self, node, protocol='tcpip', port=9000 ):
		"""AddCRUTClient(node, protocol='tcpip', port=9000)
		Tells a crutproxy node where to find a client."""
		self.__add_crut_client( node, "%s://%s:%d" % (protocol,node.host,port) )

	def __add_crut_server( self, node, url ):
		self.crutservers.append( (node, url) )
		
	def AddCRUTServer( self, node, protocol='tcpip', port=9000 ):
		self.__add_crut_server( node, "%s://%s:%d" % (protocol,node.host,port) )
		if node != None:
			node.AddCRUTClient( self, protocol, port)


class CRApplicationNode(CRNode):
	"""Sub class of CRNode that defines the start of the the SPU graph.

	public functions:

	    SetApplication:	Sets the application that generates the OpenGL.
	    StartDir:		Sets the starting directory of the app.
	    ClientDLL:		Sets the DLL of the client.
	"""
	AppID = 0

	def __init__( self, host='localhost', constraint = "name", constraintArg = None ):
		"""CRApplicationNode(host='localhost')
		Creates an application node for the given "host"."""
		CRNode.__init__(self, host,constraint,constraintArg)
		self.crutservers = []
		self.crutclients = []
		self.id = CRApplicationNode.AppID
		CRApplicationNode.AppID += 1
		self.Conf('start_dir', '.')

	def SetApplication( self, app ):
		"""SetApplication(name)
		Sets the name of the application that's run."""
		self.Conf('application', app)

	def StartDir( self, dir ):
		"""SetApplication(dir)
		Sets the directory the application starts in."""
		self.Conf('start_dir', dir)

	def ClientDLL( self, dir ):
		"""Set the directory to search for the crfaker library."""
		self.Conf('client_dll', dir)

	def __add_crut_client( self, node, url ):
		self.crutclients.append( (node, url) )

	def AddCRUTClient( self, node, protocol='tcpip', port=9000 ):
		"""AddCRUTClient(node, protocol='tcpip', port=9000)
		Tells a crutserver node where to find a client."""
		self.__add_crut_client( node, "%s://%s:%d" % (protocol,node.host,port) )

	def __add_crut_server( self, node, url ):
		self.crutservers.append( (node, url) )

	def AddCRUTServer( self, node, protocol='tcpip', port=9000 ):
		self.__add_crut_server( node, "%s://%s:%d" % (protocol,node.host,port) )
		if node != None:
			node.AddCRUTClient( self, protocol, port)


class SockWrapper:
	"""Internal convenience class for handling sockets"""
	NOERROR_MORE = 100
	NOERROR = 200
	UNKNOWNHOST = 400
	NOTHINGTOSAY = 401
	UNKNOWNCOMMAND = 402
	UNKNOWNSPU = 403
	UNKNOWNPARAM = 404
	UNKNOWNSERVER = 405
	UNKNOWNPROTOCOL = 406
	NOAPPLICATION = 407
	INVALIDPARAM = 408

	def __init__(self, sock):
		self.sock = sock # A low-level socket object
		self.file = sock.makefile( "r" )
		self.SPUid = -1
		self.node = None

		# Info for brokered network connections.  These are used to
		# implement the 'acceptrequest' and 'connectrequest' routines.

		# (hostname, port, endianness) of an outstanding accept request:
		self.tcpip_accept_wait = None

		# (hostname, port, endianness) of an outstanding connect request:
		self.tcpip_connect_wait = None

		# similar for other protocols:
		self.sdp_accept_wait = None
		self.sdp_connect_wait = None
		self.ib_accept_wait = None
		self.ib_connect_wait = None
		self.gm_accept_wait = None
		self.gm_connect_wait = None
		self.teac_accept_wait = []
		self.teac_connect_wait = []
		self.tcscomm_accept_wait = []
		self.tcscomm_connect_wait = []

	def readline( self ):
		return string.strip(self.file.readline())

	def Send(self, str):
		"""Append a newline to str and send it over the socket"""
		self.sock.send( str + "\n" )

	def Reply(self, code, s=None):
		tosend = `code`
		if s != None:
			tosend += " " + str(s)
		self.Send( tosend )
		CRDebug( 'Replying (%d): "%s"' % ( code, s ) )

	def Success( self, msg ):
		"""Send a success message over the socket"""
		self.Reply( SockWrapper.NOERROR, msg )

	def MoreComing( self, msg ):
		self.Reply( SockWrapper.NOERROR_MORE, msg )

	def Failure( self, code, msg ):
		"""Send a failure message over the socket"""
		self.Reply( code, msg )


# Generic ways to map all known node capability types
NodeTypes = { }  # key is a node type, like "faker" or "crutserver"


#
# Now, for each node type, insert a (validate, claim) tuple into the
# NodeTypes dictionary.
#

def FakerValidNode(node):
	return (not node.spokenfor and isinstance(node, CRApplicationNode))

def FakerClaim(node, sock):
	try:
		application = node.config['application']
	except:
		if sock != None:
			sock.Failure( SockWrapper.NOAPPLICATION, "Client node has no application!" )
		return
	node.spokenfor = 1
	if sock != None:
		sock.node = node
		sock.Success( "%d %s" % (node.id, application) )
NodeTypes["faker"] = (FakerValidNode, FakerClaim)

def CrutProxyValidNode(node):
	return (not node.spokenfor and isinstance(node, CRUTProxyNode))

def CrutProxyClaim(node, sock):
	node.spokenfor = 1
	if sock != None:
		sock.node = node
		sock.Success( " " )
NodeTypes["crutproxy"] = (CrutProxyValidNode, CrutProxyClaim)

def CrutServerValidNode(node):
	return (not node.spokenfor and isinstance(node, CRUTServerNode))

def CrutServerClaim(node, sock):
	node.spokenfor = 1
	if sock != None:
		sock.node = node
		sock.Success( " " )
NodeTypes["crutserver"] = (CrutServerValidNode, CrutServerClaim)

# CRUTClients are different, in that they aren't unique nodes; they're
# a subset of application nodes (that identify themselves with the "crutclient" command)
def CrutClientValidNode(node):
	return (not node.crut_spokenfor and isinstance(node, CRApplicationNode) and len(node.crutservers) > 0)
def CrutClientClaim(node, sock):
	node.crut_spokenfor = 1
	if sock != None:
		sock.node = node
		sock.Success( " " )
NodeTypes["crutclient"] = (CrutClientValidNode, CrutClientClaim)

def ServerValidNode(node):
	return (not node.spokenfor and isinstance(node, CRNetworkNode))
def ServerClaim(node, sock):
	node.spokenfor = 1
	node.spusloaded = 1
	if sock != None:
		sock.node = node
		spuchain = "%d" % len(node.SPUs)
		for spu in node.SPUs:
			spuchain += " %d %s" % (spu.ID, spu.name)
		sock.Success( spuchain )
NodeTypes["server"] = (ServerValidNode, ServerClaim)

def VNCServerValidNode(node):
	return isinstance(node, CRVNCServerNode)
def VNCServerClaim(node, sock):
	# all servers can match one VNC server node
	node.spusloaded = 1
	if sock != None:
		sock.node = node
		spuchain = "%d" % len(node.SPUs)
		for spu in node.SPUs:
			spuchain += " %d %s" % (spu.ID, spu.name)
		sock.Success( spuchain )
		CRDebug("ServerClaim returning %s" % spuchain)
NodeTypes["vncserver"] = (VNCServerValidNode, VNCServerClaim)



class CRSpawner(threading.Thread):
	"""A class used to start processes on nodes.

	Since the mothership knows what should be running on each node, it
	can start these processes if you tell it how to start a job remotely.
	Each CRNode now has members named autostart and autostart_argv. If
	you set these to non-null strings, the spawner will call os.spawnv()
	with these values when the spawner's run() member is called.

	The autostart member should be a string containing the full path
	to an executable program to be run, i.e. "/usr/bin/ssh". The
	autostart_argv member should be a vector containing the argument
	list for the program, i.e.,
		( "ssh", "mynode.mydotcom.com", "/usr/local/bin/crserver" )
	NOTE: Yes, the program name should be the zeroth item in the list
	of arguments, which means it is repeated.

	To use this class, instantiate a CRSpawner object and call its
	start() method.  Call the waitForFinish() method if you want to
	wait for the CRSpawner thread to exit/finish.
	"""
	def __init__( self, nodes, branches=0, maxnodes=1):
		self.maxnodes = maxnodes
		self.branches = branches
		self.nodes = []
		self.count = 0
		for node in nodes:
			self.nodes.append( node )
			self.count = self.count + 1
		threading.Thread.__init__(self)
	def run( self ):
		if self.branches < 2 or self.count <= self.maxnodes:
			# This thread will sequentially spawn all listed nodes.
			for node in self.nodes:
				if node.autostart != "":
					p = os.spawnv( os.P_NOWAIT, node.autostart, node.autostart_argv )
					CRInfo("Autostart for node %s: %s" % (node.host, str(node.autostart_argv)))
				else:
					if isinstance(node, CRNetworkNode):
						CRInfo("Start a crserver on %s" % node.host)
					elif isinstance(node, CRUTServerNode):
						CRInfo("Start a crutserver on %s" % node.host)
					elif isinstance(node, CRUTProxyNode):
						CRInfo("Start a crutproxy on %s" % node.host)
					else:
						CRInfo("Start a crappfaker on %s" % node.host)
		else:
			# We have more nodes than we want to handle in this
			# thread.  Instead of spawning processes, create new
			# threads, and have those threads handle pieces of the
			# nodes.
			childsize = int((self.count + self.branches - 1)/self.branches)
			for i in range(0, self.count, childsize):
				child = CRSpawner(self.nodes[i:i+childsize], self.branches, self.maxnodes)
				child.start()
	#enddef

	def waitForFinish(self):
		"""This method won't return until this thread has completed."""
		# NOTE: the join() method doesn't seem to do what we want.
		while 1:
			if self.isAlive():
				time.sleep(1)  # Wait a second, then try again
			else:
				return
	#enddef


class CR:
	"""Main class that controls the mothership

        Most of the mothership network communication takes the form of
        incoming strings that the mothership responds to with answer
        strings.  The do_* functions handle this communication language.

	public functions:
	    AddNode: Adds a node to the SPU graph.
	    MTU: Sets the maximum communication buffer size.
	    Go: Starts the ball rolling.
	    AllSPUConf: Adds the key/values list to all SPUs' configuration.
	    Conf: Set a mothership parameter
	    GetConf: Return value of a mothership parameter
	    ContextRange: Sets the Quadrics context range.
	    NodeRange: Sets the Quadrics node range.
	    CommKey: Sets the Quadrics communication key

	internal functions:
	    ProcessRequest:	Handles an incoming request, mapping it to
				an appropriate do_* function.
	    do_acceptrequest:	Accepts the given socket.
	    do_clients: 	Sends the list of clients to a server.
	    do_connectrequest:	Connects the given socket.
	    do_faker:		Maps a faker app to an ApplicationNode.
	    do_opengldll:	Identifies the application node in the graph.
	    do_rank:		Sends the node's rank down.
	    do_disconnect: 	Disconnects from clients.
	    do_reset: 		Resets the mothership to its initial state.
	    do_server:		Identifies the server in the graph.
	    do_vncserver:	Identifies a new server for VNC replication.
	    do_serverids:	Sends the list of server IDs.
	    do_serverparam:	Sends the given server parameter.
	    do_fakerparam:	Sends the given app faker parameter.
	    do_servers: 	Sends the list of servers.
	    do_servertiles: Sends the defined tiles for a server.
	    do_spu:			Identifies a SPU.
	    do_spuparam:	Sends the given SPU (or global) parameter.
	    do_tiles:		Sends the defined tiles for a SPU.
	    do_setparam:	Sets a mothership parameter value
	    do_getparam:	Returns a mothership parameter value
	    do_logperf:		Logs Performance Data to a logfile.
	    do_gettilelayout:   Calls the user's LayoutTiles() function and returns
							the list of new tiles.
	    do_getstatus:	Returns information about the state of the nodes.
	    tileReply: 		Packages up a tile message for socket communication.
	    ClientDisconnect: 	Disconnects from a client
	"""
	startupCallbacks = []
	
	def __init__( self ):
		self.nodes = []    # list of all nodes
		self.all_sockets = []
		self.wrappers = {} # list of SockWrapper objects, indexed by socket no.
		self.allSPUConf = []
		self.daughters = []
		self.conn_id = 1   # Next free connection ID number
		self.enable_autostart = 1
		self.config = {"MTU" : 1024 * 1024,
				"low_context" : 32,
				"high_context" : 35,
				"low_node" : "iam0",
				"high_node" : "iamvis20",
				"comm_key": [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],
				"autostart_branches": 0,
				"autostart_max_nodes_per_thread": 1}
		# This is set only on daughterships; "grandmotherships" don't
		# have mothers, and never have pending resolutions.
		self.mother = None
		self.pendingResolution = [ ]

	def AddNode( self, node ):
		"""Adds a node to the Mothership."""
		node.nodeIndex = len(self.nodes)  # assign the node's ID now
		self.nodes.append( node )
	
	def Conf( self, key, value ):
		"""Set a global mothership configuration value (via Python)"""
		self.config[key] = value

	def ContextRange( self, low_context, high_context ):
		"""ContextRange( low_context, high_context )
		Sets the context range to use with Elan."""
		self.config["low_context"]  = low_context
		self.config["high_context"] = high_context

	def NodeRange( self, low_node, high_node ):
		"""NodeRange( low_node, high_node )
		Sets the node range to use with Elan."""
		period = low_node.find( "." )
		if period != -1:
			low_node = low_node[:period]
		self.config["low_node"]  = low_node
		period = high_node.find( "." )
		if period != -1:
			high_node = high_node[:period]
		self.config["high_node"] = high_node

	def CommKey( self, byteList ):
		"""CommKey( [byte0, byte1, ..., byte15] )
		Sets the user key to use with Elan."""
		self.config["comm_key"]= byteList
		CRDebug("Setting comm key to %s"%str(byteList))

	def AllSPUConf( self, regex, key, *values ):
		"""AllSPUConf(regex, key, *values)
		Adds the key/values list to the global SPU configuration."""
		self.allSPUConf.append( (regex, key, map( MakeString, values) ) )

	# XXX obsolete; use Conf('MTU', value) instead
	def MTU( self, mtu ):
		"""MTU(size)
		Sets the maximum buffer size allowed in communication
		between SPUs."""
		self.Conf("MTU", mtu)

	def FindSPUHost( self, spu ):
		"""Seach all nodes to find the one that hosts the given SPU."""
		for node in self.nodes:
			for s in node.SPUs:
				if s == spu:
					return node
		return None

	def do_setparam( self, sock, args ):
		"""Set a global mothership parameter value (via C)"""
		params = args.split( " ", 1 )
		key = params[0]
		value = params[1]
		self.Conf(key, value)
		sock.Success( "OK" )
		return

	def do_getparam( self, sock, args ):
		"""Get a global mothership parameter value (via C)"""
		key = args
		if not self.config.has_key(key):
			response = ""
		else:
			response = str(self.config[key])
		sock.Success( response )
		return

	def do_exit( self, sock, args ):
		"""This is called in response to an 'exit' message from a client."""
		raise KeyboardInterrupt

	def get_mothership_port(self):
		"""Get the mothership port.  Use CRMOTHERSHIP env var if it's set,
		otherwise return default value."""
		# Port was not specified.  Get it from
		# CRMOTHERSHIP environment variable if possible..
		PORT = DefaultMothershipPort  # default value
		if os.environ.has_key('CRMOTHERSHIP'):
			motherString = os.environ['CRMOTHERSHIP']
			loc = string.find(motherString,':')
			if loc >= 0:
				try:
					PORT = int(motherString[loc+1:])
					CRDebug("Using PORT %d"%PORT)
				except Exception, val:
					CRInfo("Could not parse port number from <%s>: %s"%(motherString,val))
					CRInfo("Using default PORT!")
		return PORT


	def Go( self, PORT = -1 ):
		"""Go(portNumber)
		Starts the ball rolling.
		This starts the mothership's event loop.
		The optional parameter is the mothership port we'll listen on."""
		if self.mother:
			CRInfo("This is Chromium Daughtership, Version " + Version)
			# You must always listen to your mother.
			self.all_sockets.append(self.mother.sock)
			self.wrappers[self.mother.sock] = self.mother
		else:
			CRInfo("This is Chromium, Version " + Version)
		try:
			if PORT == -1:
				PORT = self.get_mothership_port()

			for res in socket.getaddrinfo(None, PORT, socket.AF_UNSPEC, socket.SOCK_STREAM, 0, socket.AI_PASSIVE):
				(af, socktype, proto, canonname, sa) = res

				try:
					s = socket.socket( af, socktype )
				except:
					CRDebug( "Couldn't create socket of family %u, trying another one" % af )
					continue

				try:
					s.setsockopt( socket.SOL_SOCKET, socket.SO_REUSEADDR, 1 )
				except:
					CRDebug( "Couldn't set the SO_REUSEADDR option on the socket!" )
					continue

				try:
					s.bind( sa )
				except:
					CRDebug( "Couldn't bind to port %d" % PORT )
					continue

				try:
					s.listen(100)
				except:
					CRDebug( "Couldn't listen!" )
					continue

				#CRDebug( "Mothership ready" )
				self.all_sockets.append(s)

				# Call any callbacks which may have been
				# set via CRAddStartupCallback()
				for cb in CR.startupCallbacks:
					cb(self)

				# Create a single thread that will then go
				# spawn nodes (for autostart nodes, this will
				# actually start the servers or applications
				# itself; for manual start nodes, a message
				# will be printed directing the user to start
				# the appropriate executable).
				#
				# This thread will either sequentially handle
				# all nodes (by default, if autostart_branches=None)
				# or will create a number of new threads (quite
				# possibly recursively) to handle subsets of the
				# nodes (if autostart_branches is greater than 1, 
				# this will be a tree of threads).
				if self.enable_autostart:
					spawner = CRSpawner(self.nodes, self.config['autostart_branches'], self.config['autostart_max_nodes_per_thread'])
					spawner.start()
					spawner.waitForFinish()

				# If we're supposed to "phone home" with a signal, do so
				# with the USR1 signal.  This will happen when we're
				# auto-starting on Linux - the OpenGL stub will wait
				# until the mothership is going before attempting to
				# make contact.  (The CRSIGNAL envariable should never
				# be set on Windows, since Windows Python doesn't
				# seem to support os.kill().)
				needToSignal = 0
				if os.environ.has_key('CRSIGNAL'):
					needToSignal = 1

				# Begin main mothership loop here.  Accept new connections
				# from Chromium components and process configuration/etc
				# requests.
				while 1:
					# We can only safely signal the mothership when all the
					# dynamic nodes have been resolved; this is because the
					# main application will ask about what servers are available,
					# and we don't know the answer until all dynamic nodes are
					# resolved.  Note that this essentially prevents Windows
					# users from using dynamic hosts, because they cannot signal.
					if needToSignal and len(dynamicHostsNeeded) == 0:
						process = int(os.environ['CRSIGNAL'])
						CRInfo("Mothership signalling spawning process %d" % process)
						os.kill(process,signal.SIGUSR1)
						needToSignal = 0

					ready = select.select( self.all_sockets, [], [], 0.1 )[0]
					for sock in ready:
						if sock == s:
							# accept a new connection
							(conn, addr) = s.accept()
							self.wrappers[conn] = SockWrapper(conn)
							self.all_sockets.append( conn )
						else:
							# process request from established connection
							self.ProcessRequest( self.wrappers[sock] )
					#end for
				# end while
			# endfor

			# if we get here we weren't able to create the mothership's port
			Fatal( "Couldn't find/create local TCP port (make sure that another mothership isn't already running)")
		except KeyboardInterrupt:
			try:
				for sock in self.all_sockets:
					sock.shutdown(2)
					sock.close( )
			except:
				pass
			CRInfo("\n\nThank you for using Chromium!")
		except:
			CRInfo("\n\nMOTHERSHIP EXCEPTION!  TERRIBLE!")
			traceback.print_exc(None, sys.stderr)
			try:
				for sock in self.all_sockets:
					sock.shutdown(2)
					sock.close( )
			except:
				pass

	def ClientError( self, sock_wrapper, code, msg ):
		"""ClientError(sock_wrapper, code, msg)
		Sends an error message on the given socket."""
		sock_wrapper.Reply( code, msg )
		self.ClientDisconnect( sock_wrapper )
		
	def ClientDisconnect( self, sock_wrapper ):
		"""ClientDisconnect(sock_wrapper)
		Disconnects from the client on the given socket."""
		self.all_sockets.remove( sock_wrapper.sock )
		del self.wrappers[sock_wrapper.sock]
		try:
			sock_wrapper.sock.close( )
		except:
			pass


	def ConnectTCPIP( self, sock, connect_info ):
		"""Connect routine for TCP/IP (see do_connectrequest())"""
		(p, hostname, port_str, endianness_str) = connect_info
		assert p == "tcpip"
		hostname = socket.gethostbyname(QualifyHostname(hostname))
		port = int(port_str)
		endianness = int(endianness_str)
		# Loop over all of the mothership's socket wrappers, looking for
		# a socket which has an Accept pending on the same host and port.
		# When found, return a new connection ID.
		for server_sock in self.wrappers.values():
			if server_sock.tcpip_accept_wait != None:
				(server_hostname, server_port, server_endianness) = server_sock.tcpip_accept_wait

				if SameHost(server_hostname, hostname) and server_port == port:
					sock.Success("%d %d" % (self.conn_id, server_endianness))
					# reply to the waiting server
					server_sock.Success( "%d" % self.conn_id )
					# we don't want to re-use this info!!
					server_sock.tcpip_accept_wait = None
					self.conn_id += 1
					return
				else:
					CRDebug( "not connecting to \"%s:%d\" (!= \"%s:%d\")"
							 % (server_hostname, server_port, hostname, port) )
			else:
				CRDebug("tcpip_accept_wait")
		# If we get here, the other end of the connection hasn't contacted
		# the mothership yet.  So, save this request's hostname, port and
		# endianness for when the matching "acceptrequest" message comes in.
		# When we get it, we'll finally reply on the saved client socket.
		sock.tcpip_connect_wait = (hostname, port, endianness)
		return

	def ConnectSDP( self, sock, connect_info ):
		"""Connect routine for SDP (see do_connectrequest())"""
		(p, hostname, port_str, endianness_str) = connect_info
		hostname = socket.gethostbyname(QualifyHostname(hostname))
		port = int(port_str)
		endianness = int(endianness_str)
		for server_sock in self.wrappers.values():
			if server_sock.sdp_accept_wait != None:
				(server_hostname, server_port, server_endianness) = server_sock.sdp_accept_wait
				if SameHost(server_hostname, hostname) and server_port == port:
					sock.Success("%d %d" % (self.conn_id, server_endianness))
					server_sock.Success( "%d" % self.conn_id )
					# we don't want to re-use this info!!
					server_sock.sdp_accept_wait = None
					self.conn_id += 1
					return
				else:
					CRDebug( "not connecting to \"%s:%d\" (!= \"%s:%d\")"
							 % (server_hostname, server_port, hostname, port) )
		sock.sdp_connect_wait = (hostname, port, endianness)
		return	

	def ConnectIB( self, sock, connect_info ):
		"""Connect routine for InfiniBand (see do_connectrequest())"""
		(p, hostname, port_str, node_id_str, endianness_str, lid1, qp_ous, qp) = connect_info
		CRInfo("do_connectrequest processing ib protocol")
		hostname = socket.gethostbyname(QualifyHostname(hostname))
		port = int(port_str)
		node_id = int(node_id_str)
		endianness = int(endianness_str)
		for server_sock in self.wrappers.values():
			if server_sock.ib_accept_wait != None:
				(server_hostname, server_port, server_node_id, server_endianness, server_lid1, server_qp_ous, server_qp) = server_sock.ib_accept_wait
				if SameHost(server_hostname, hostname) and server_port == port:
					sock.Success( "%d %d %d %s %s %s" % (self.conn_id, server_node_id, server_endianness, server_lid1, server_qp_ous, server_qp ) )
					server_sock.Success( "%d %d %s %s %s" % (self.conn_id, node_id, lid1, qp_ous, qp ) )
					# we don't want to re-use this info!!
					server_sock.ib_accept_wait = None
					self.conn_id += 1
					return
				else:
					CRDebug( "not connecting to \"%s:%d\" (!= \"%s:%d\")"
							 % (server_hostname, server_port, hostname, port) )
		sock.ib_connect_wait = (hostname, port, node_id, endianness, lid1, qp_ous, qp)
		return

	def ConnectGM( self, sock, connect_info ):
		"""Connect routine for GM (see do_connectrequest())"""
		(p, hostname, port_str, node_id_str, port_num_str, endianness_str) = connect_info
		port = int(port_str)
		node_id = int(node_id_str)
		port_num = int(port_num_str)
		endianness = int(endianness_str)
		for server_sock in self.wrappers.values():
			if server_sock.gm_accept_wait != None:
				(server_hostname, server_port, server_node_id, server_port_num, server_endianness) = server_sock.gm_accept_wait
				if SameHost(server_hostname, hostname) and server_port == port:
					sock.Success( "%d %d %d %d" % (self.conn_id, server_node_id, server_port_num, server_endianness) )
					server_sock.Success( "%d %d %d" % (self.conn_id, node_id, port_num) )
					server_sock.gm_accept_wait = None
					self.conn_id += 1
					return
		sock.gm_connect_wait = (hostname, port, node_id, port_num, endianness)
		return

	def ConnectQuadrics( self, sock, connect_info ):
		"""Connect routine for Quadrics (see do_connectrequest())"""
		(p, remote_hostname, remote_rank_str, my_hostname, my_rank_str, my_endianness_str) = connect_info
		remote_rank = int(remote_rank_str)
		my_rank = int(my_rank_str)
		my_endianness = int(my_endianness_str)
		for server_sock in self.wrappers.values():
			if server_sock.teac_accept_wait != []:
				(server_hostname, server_rank, server_endianness) = server_sock.teac_accept_wait[0]
				if SameHost(server_hostname, remote_hostname) and server_rank == remote_rank:
					server_sock.teac_accept_wait.pop(0)
					sock.Success( "%d %d" % (self.conn_id, server_endianness) )
					server_sock.Success( "%d %s %d %d" % (self.conn_id, my_hostname, my_rank, my_endianness) )
					self.conn_id += 1
					return
		sock.teac_connect_wait.append( (my_hostname, my_rank, my_endianness, remote_hostname, remote_rank) )
		return

	def ConnectTcscomm( self, sock, connect_info ):
		"""Connect routine for Quadrics-Tcscomm (see do_connectrequest())"""
		(p, remote_hostname, remote_rank_str, my_hostname, my_rank_str, my_endianness_str) = connect_info
		remote_rank = int(remote_rank_str)
		my_rank = int(my_rank_str)
		my_endianness = int(my_endianness_str)
		for server_sock in self.wrappers.values():
			if server_sock.tcscomm_accept_wait != []:
				(server_hostname, server_rank, server_endianness) = server_sock.tcscomm_accept_wait[0]
				if SameHost(server_hostname, remote_hostname) and server_rank == remote_rank:
					server_sock.tcscomm_accept_wait.pop(0)
					sock.Success( "%d %d" % (self.conn_id, server_endianness) )
					server_sock.Success( "%d %s %d %d" % (self.conn_id, my_hostname, my_rank, my_endianness) )
					self.conn_id += 1
					return
		sock.tcscomm_connect_wait.append( (my_hostname, my_rank, my_endianness, remote_hostname, remote_rank) )
		return

	def do_connectrequest( self, sock, args ):
		"""
		This function is called when the mothership receives a "connectrequest"
		message from a network-specific Connect() function (in "util/") if the
		connection is brokered.
		We call a network-specific connect routine above which returns its
		response on the given socket.
		"""
		connect_info = args.split(" ")
		protocol = connect_info[0]
		if protocol == 'tcpip' or protocol == 'udptcpip':
			self.ConnectTCPIP(sock, connect_info)
		elif protocol == 'sdp':
			self.ConnectSDP(sock, connect_info)
		elif protocol == 'ib':
			self.ConnectIB(sock, connect_info)
		elif protocol == 'gm':
			self.ConnectGM(sock, connect_info)
		elif protocol == 'quadrics':
			self.ConnectQuadrics(sock, connect_info)
		elif protocol == 'quadrics-tcscomm':
			self.ConnectTcscomm(sock, connect_info)
		else:
			sock.Failure(SockWrapper.UNKNOWNPROTOCOL,
						 "Never heard of protocol %s" % protocol)
		return


	def AcceptTCPIP( self, sock, accept_info ):
		"""Accept routine for TCP/IP (see do_acceptrequest())"""
		(p, hostname, port_str, endianness_str) = accept_info
		assert p == "tcpip"
		# If the mothership doesn't recognize the remote host,
		# the QualifyHostname() call can fail with a cryptic exception.
		# This is fatal; but we can still give a better error
		# than the cryptic message.
		try:
			hostname = socket.gethostbyname(QualifyHostname(hostname))
#			print "accept: " + hostname
		except:
			Fatal( "Mothership error: could not qualify hostname '%s' - check /etc/hosts" % hostname)

		port = int(port_str)
		endianness = int(endianness_str)
		# Loop over all of the mothership's socket wrappers, looking for
		# a socket which has a Connect pending on the same host and port.
		# When found, return a new connection ID and the server's endianness.
		for client_sock in self.wrappers.values():
			if client_sock.tcpip_connect_wait != None:
				(client_hostname, client_port, client_endianness) = client_sock.tcpip_connect_wait
				if SameHost(client_hostname, hostname) and client_port == port:
					sock.Success( "%d" % self.conn_id )
					# reply to the waiting client
					client_sock.Success("%d %d" % (self.conn_id, endianness))
					# we don't want to re-use this info!!
					client_sock.tcpip_connect_wait = None
					self.conn_id += 1
					return
				else:
					CRDebug( "not accepting from \"%s:%d\" (!= \"%s:%d\")" % (client_hostname, client_port, hostname, port ) )
			else:
				CRDebug( "tcpip_connect_wait" )
		# If we get here, the other end of the connection hasn't contacted
		# the mothership yet.  So, save this request's hostname, port and
		# endianness for when the matching "connectrequest" message comes in.
		# When we get it, we'll finally reply on the saved server socket.
		sock.tcpip_accept_wait = (hostname, port, endianness)
		return

	def AcceptSDP( self, sock, accept_info ):
		"""Accept routine for SDP (see do_acceptrequest())"""
		(p, hostname, port_str, endianness_str) = accept_info
		hostname = socket.gethostbyname(QualifyHostname(hostname))
		port = int(port_str)
		endianness = int(endianness_str)
		for client_sock in self.wrappers.values():
			if client_sock.sdp_connect_wait != None:
				(client_hostname, client_port, client_endianness) = client_sock.sdp_connect_wait
				if SameHost(client_hostname, hostname) and client_port == port:
					sock.Success( "%d" % self.conn_id )
					client_sock.Success("%d %d" % (self.conn_id, endianness))
					# we don't want to re-use this info!!
					client_sock.sdp_connect_wait = None
					self.conn_id += 1
					return
				else:
					CRDebug( "not accepting from \"%s:%d\" (!= \"%s:%d\")" % (client_hostname, client_port, hostname, port ) )
			else:
				CRDebug( "sdp_connect_wait" )
		sock.sdp_accept_wait = (hostname, port, endianness)
		return

	def AcceptIB( self, sock, accept_info ):
		"""Accept routine for InfiniBand (see do_acceptrequest())"""
		(p, hostname, port_str, node_id_str, endianness_str, lid1, qp_ous, qp) = accept_info
		CRInfo("do_acceptrequest processing ib protocol")
		hostname = socket.gethostbyname(hostname)
		port = int(port_str)
		node_id = int(node_id_str)
		endianness = int(endianness_str)
		for client_sock in self.wrappers.values():
			if client_sock.ib_connect_wait != None:
				(client_hostname, client_port, client_node_id,
				 client_endianness, client_lid1, client_qp_ous, client_qp) = client_sock.ib_connect_wait
				if SameHost(client_hostname, hostname) and client_port == port:
					sock.Success( "%d %d %s %s %s" % (self.conn_id, client_node_id, client_lid1, client_qp_ous, client_qp ) )
					client_sock.Success( "%d %d %d %s %s %s" % (self.conn_id, node_id, endianness, lid1, qp_ous, qp ) )
					# we don't want to re-use this info!!
					client_sock.ib_connect_wait = None
					self.conn_id += 1
					return
				else:
					CRDebug( "not accepting from \"%s:%d\" (!= \"%s:%d\")" % (client_hostname, client_port, hostname, port ) )
			else:
				CRDebug( "ib_connect_wait" )
		sock.ib_accept_wait = (hostname, port, node_id, endianness, lid1, qp_ous, qp)
		return

	def AcceptGM( self, sock, accept_info ):
		"""Accept routine for GM (see do_acceptrequest())"""
		(p, hostname, port_str, node_id_str, port_num_str, endianness_str) = accept_info
		port = int(port_str)
		node_id = int(node_id_str)
		port_num = int(port_num_str)
		endianness = int(endianness_str)
		for client_sock in self.wrappers.values():
			if client_sock.gm_connect_wait != None:
				(client_hostname, client_port, client_node_id, client_port_num, client_endianness) = client_sock.gm_connect_wait
				if SameHost(client_hostname, hostname) and client_port == port:
					sock.Success( "%d %d %d" % (self.conn_id, client_node_id, client_port_num) )
					client_sock.Success( "%d %d %d %d" % (self.conn_id, node_id, port_num, endianness) )
					self.conn_id += 1
					client_sock.gm_connect_wait = None
					return
		sock.gm_accept_wait = (hostname, port, node_id, port_num, endianness)
		return
	
	def AcceptQuadrics( self, sock, accept_info ):
		"""Accept routine for Quadrics (see do_acceptrequest())"""
		(p, hostname, rank_str, endianness_str) = accept_info
		rank = int(rank_str)
		endianness = int(endianness_str)
		for client_sock in self.wrappers.values():
			if client_sock.teac_connect_wait != []:
				(client_hostname, client_rank, client_endianness, server_hostname, server_rank) = client_sock.teac_connect_wait[0]
				if SameHost(server_hostname, hostname) and server_rank == rank:
					client_sock.teac_connect_wait.pop(0)
					sock.Success( "%d %s %d %d" % (self.conn_id, client_hostname, client_rank, client_endianness) )
					client_sock.Success( "%d %d" % (self.conn_id, endianness) )
					self.conn_id += 1
					return
		sock.teac_accept_wait.append( (hostname, rank, endianness) )
		return

	def AcceptTcscomm( self, sock, accept_info ):
		"""Accept routine for Quadrics-Tcscomm (see do_acceptrequest())"""
		(p, hostname, rank_str, endianness_str) = accept_info
		rank = int(rank_str)
		endianness = int(endianness_str)
		for client_sock in self.wrappers.values():
			if client_sock.tcscomm_connect_wait != []:
				(client_hostname, client_rank, client_endianness, server_hostname, server_rank) = client_sock.tcscomm_connect_wait[0]
				if SameHost(server_hostname, hostname) and server_rank == rank:
					client_sock.tcscomm_connect_wait.pop(0)
					sock.Success( "%d %s %d %d" % (self.conn_id, client_hostname, client_rank, client_endianness) )
					client_sock.Success("%d %d" % (self.conn_id, endianness))
					self.conn_id += 1
					return
		sock.tcscomm_accept_wait.append( (hostname, rank, endianness) )
		return

	def do_acceptrequest( self, sock, args ):
		"""
		This function is called when the mothership receives a "acceptrequest"
		message from a network-specific Accept() function (in "util/") if the
		connection is brokered.
		We call a network-specific accept routine above which returns its
		response on the given socket.
		"""
		accept_info = args.split(" ")
		protocol = accept_info[0]
		if protocol == 'tcpip' or protocol == 'udptcpip':
			self.AcceptTCPIP(sock, accept_info)
		elif protocol == 'sdp':
			self.AcceptSDP(sock, accept_info)
		elif protocol == 'ib':
			self.AcceptIB(sock, accept_info)
		elif protocol == 'gm':
			self.AcceptGM(sock, accept_info)
		elif protocol == 'quadrics':
			self.AcceptQuadrics(sock, accept_info)
		elif protocol == 'quadrics-tcscomm':
			self.AcceptTcscomm(sock, accept_info)
		else:
			sock.Failure(SockWrapper.UNKNOWNPROTOCOL,
						 "Never heard of protocol %s" % protocol)

	def MatchNode(self, nodeTypeName, sock, args):
		""" A (too?) clever routine.  This handles all the work of matching
		various types of nodes, with static matches or with dynamic matches.
		It even handles dynamic resolution and errors.
		Input: args: the hostname of the caller
		Return: a node reference
		"""
		try:
			(validFunc, claimFunc) = NodeTypes[nodeTypeName]
		except:
			print "*** ERROR: trying to match unknown node type '%s'" % nodeTypeName
			return None

		# Try first to resolve the host with a static match
		nodenames = ""
		listedNodenames = { }
		for node in self.nodes:
			if validFunc(node):
				# Record all the static node names for a message later
				if not node.dynamic_host and not listedNodenames.has_key(node.host):
					listedNodenames[node.host] = 1
					nodenames += node.host+" "
				if MatchStaticNode(node,args):
					claimFunc(node, sock)
					return node


		# No static node matches.  Try dynamic nodes that are already resolved.
		for node in self.nodes:
			if validFunc(node):
				if node.dynamic_host and not listedNodenames.has_key("[dynamic]"):
					listedNodenames["[dynamic]"] = 1
					nodenames += "[dynamic] "
				if MatchResolvedNode(node,args):
					claimFunc(node, sock)
					return node

		# If unresolved nodes are present, we can try to resolve them.
		if len(dynamicHostsNeeded) > 0:
			# Only the "grandmothership" (i.e., a mothership with no mother)
			# may resolve nodes.
			if not self.mother: # i.e. I'm the grandmother
				index = 0
				for node in self.nodes:
					if validFunc(node) and MatchUnresolvedNode(node,args):
						# We matched the server with an appropriate node.
						# Tell the daughters.
						self.Broadcast(self.daughters, "match %d %s" % (index, args))
						claimFunc(node, sock)
						return node
					index += 1
			else:
				# A daughtership must ask its mother to resolve nodes; the
				# answer will come back asynchronously, so we'll have to
				# save our request and deal with it later.
				# When we get the match back, we'll pull all matching pending
				# resolutions from here and restart their processing.
				# The exception raised prevents the main routine (which
				# called us) from continuing with normal processing.
				self.mother.Send("requestmatch %s %s" % (nodeTypeName, args))
				self.pendingResolution.append( ("do_%s" % nodeTypeName, sock, args) )
				return node

		# Nothing matches, and we've tried most everything.
		if sock != None:
			sock.Failure( SockWrapper.UNKNOWNHOST, "Never heard of %s host %s.  Expected one of: %s" % (nodeTypeName, args, nodenames))
		return None

	def Broadcast(self, sockets, message):
		for s in sockets:
			s.Send(message)

	def do_faker( self, sock, args ):
		"""do_faker(sock, args)
		Maps the incoming "faker" app to a previously-defined node.  I.e.
		crappfakers identify themselves to the mothership with this message.
		Will return to the crappfaker the command line arguments for starting
		the OpenGL application."""
		self.MatchNode( "faker", sock, args)

	def do_vncserver( self, sock, args ):
		"""do_newserver(sock, args)
		Called by a crserver to identify itself as a VNC/replication server."""
		# One CRVNCServerNode instance will match any number of crservers.
		# NOTE: we ignore args (the hostname)
		self.MatchNode("vncserver", sock, "anyhost")

	def do_crutproxy( self, sock, args ):
		CRDebug ( " Seeing if we have a crutproxy." )
		"""do_crutserver(sock, args)
		Hopefully tells us that we have a crutserver running somewhere."""
		self.MatchNode("crutproxy", sock, args)
		
	def do_crutserver( self, sock, args ):
		"""do_crutserver(sock, args)
		Hopefully tells us that we have a crutserver running somewhere."""
		self.MatchNode("crutserver", sock, args)

	def do_crutclient( self, sock, args ):
		"""do_crutserver(sock, args)
		Hopefully tells us that we have a crutclient running somewhere."""
		self.MatchNode("crutclient", sock, args)

	def do_server( self, sock, args ):
		"""do_server(sock, args)
		Servers send this message to identify themselves to the mothership."""
		self.MatchNode("server", sock, args)

	def do_match(self, sock, args):
		"""
		This can either come in as a result of a request we made for a match,
		or spontaneously (to notify us of a match the mothership has made).
		We are to notify our daughters, log the node ourselves, and to
		release and activate any resolutions that were waiting on this node.
		"""
		self.Broadcast(self.daughters, "match %s" % args)

		words = string.split(args)
		node = self.nodes[int(words[0])]
		hostname = words[1]

		ResolveNode(node, hostname)
		stillUnresolved = []
		for (pendingCommand, pendingSock, pendingHost) in self.pendingResolution:
			if MatchResolvedNode(node, pendingHost):
				fn = getattr(self, pendingCommand)
				fn(pendingSock, pendingHost)
			else:
				stillUnresolved.append((pendingCommand, pendingSock, pendingHost))
		self.pendingResolution = stillUnresolved

	def do_requestmatch(self, sock, args):
		"""
		This can only come from a daughter to a mother.  If we're the
		grandmother, we process it.  Otherwise, we pass it up.  We'll
		eventually get a "match" command back, with information we need.
		"""
		if self.mother:
			self.mother.Send("requestmatch %s" % args)
			return
		# Here, we're the grandmother.  We can resolve this by doing a normal
		# match at our level.  Note that we don't really have a socket - the
		# socket we are passed is a daughter, not the real client.
		words = string.split(args)
		nodeTypeName = words[0]
		hostName = words[1]
		node = self.MatchNode(nodeTypeName, None, hostName)
		if node == None:
			# This is bad.  Daughters will likely hang, failing to respond
			# to connections, because they're waiting for a match.
			print "*** ERROR: requestmatch couldn't match a node!"
			return

		# The MatchNode method will already have passed the necessary information
		# on to the daughterships, so we don't have to do it again.
		return

	def do_opengldll( self, sock, args ):
		"""do_opengldll(sock, args)
		The OpenGL faker library (libcrfaker.so) identifies itself to the
		mothership with this message.  Returns the client's SPU chain."""
		(id_string, hostname) = args.split( " " )
		app_id = int(id_string)
		for node in self.nodes:
			if isinstance(node,CRApplicationNode):
				if ((app_id == -1 and SameHost(hostname, node.host)) or node.id == app_id) and not node.spusloaded:
					node.spusloaded = 1
					spuchain = "%d" % len(node.SPUs)
					for spu in node.SPUs:
						spuchain += " %d %s" % (spu.ID, spu.name)
					sock.Success( spuchain )
					sock.node = node
					return
		# If you get this error message and don't know why, check if there's
		# a stale mothership process still running.
		sock.Failure(SockWrapper.UNKNOWNHOST, "Unexpected identification message from crfaker (app %d)" % app_id)

	def do_spu( self, sock, args ):
		"""do_spu(sock, args)
		SPUs send this message to the mothership to identify themselves."""
		try:
			spuid = int(args)
		except:
			sock.Failure( SockWrapper.UNKNOWNSPU, "Bogus SPU name: %s" % args )
			return
		if not allSPUs.has_key( spuid ):
			sock.Failure( SockWrapper.UNKNOWNSPU, "Never heard of SPU %d" % spuid )
			return
		sock.SPUid = spuid
		sock.Success( "Hello, %s SPU!" % allSPUs[spuid].name )

	def do_spuparam( self, sock, args ):
		"""do_spuparam(sock, args)
		Sends the given SPU (or global) parameter."""
		if sock.SPUid == -1:
			sock.Failure( SockWrapper.UNKNOWNSPU, "You can't ask for SPU parameters without telling me what SPU id you are!" )
			return
		spu = allSPUs[sock.SPUid]
		if not spu.config.has_key( args ):
			# Okay, there's no specific parameter for the SPU.
			# Try the global SPU configurations.
			for (regex, key, values) in self.allSPUConf:
				if args == key and re.search( regex, spu.name ) != -1:
					response = values
					break
			else:
				sock.Failure( SockWrapper.UNKNOWNPARAM,
							  "SPU %d (%s) doesn't have param %s"
							  % (sock.SPUid, allSPUs[sock.SPUid].name, args) )
				return
		else:
			response = spu.config[args]
		CRDebug("responding with args = " + `response`)
#		sock.Success( string.join( response, " " ) )
		sock.Success( response )

	def do_get_spu_rank( self, sock, args ):
		"""When a number of SPUs are connected to a server, this function
		will return the rank/index of this SPU with respect to the server.
		For example, if there are three pack SPUs connected to a server and
		each pack SPU calls this function, we'll uniquely return "0", "1"
		and "2" to the those SPUs."""
		if sock.SPUid == -1:
			sock.Failure( SockWrapper.UNKNOWNSPU,
						  "You can't ask for SPU peers without telling me" +
						  "what SPU id you are!" )
			return
		### This is a bit tricky.  Some searching is involved.
		# Find the last SPU in SPU chain that I belong to.
		spu = allSPUs[sock.SPUid]
		spuHost = self.FindSPUHost(spu)
		lastSPU = spuHost.SPUs[-1]
		# Check if there's no upstream server node.
		if len(lastSPU.servers) == 0:
			sock.Success( "0" )
			return
		# Get the last SPU's server node.
		(serverNode, url) = lastSPU.servers[0]
		rank = 0
		# Loop over client SPUs of the server node.
		for (clientSpu, protocol) in serverNode.GetClients():
			# Find the node that hosts this SPU.
			clientNode = self.FindSPUHost(clientSpu)
			# Try to find target SPU in this node's SPU chain.
			for s in clientNode.SPUs:
				if s == spu:
					# Found it!
					sock.Success( str(rank) )
					return
			rank += 1
		# Strange, maybe this SPU is on a crserver.
		sock.Success( "-1" )

	def do_crutserverparam( self, sock, args ):
		"""do_crutserverparam(sock, args)
		Sends the given crutserver parameter."""
		if sock.node == None or not isinstance(sock.node,CRUTServerNode):
			sock.Failure( SockWrapper.UNKNOWNSERVER, "You can't ask for server parameters without telling me what crutserver you are!" )
			return
		if not sock.node.config.has_key( args ):
			sock.Failure( SockWrapper.UNKNOWNPARAM, "Server doesn't have param %s" % (args) )
			return
		#sock.Success( string.join( sock.node.config[args], " " ) )
		sock.Success( sock.node.config[args] )

	def do_serverparam( self, sock, args ):
		"""do_serverparam(sock, args)
		Sends the given server parameter."""
		if sock.node == None or not (isinstance(sock.node,CRNetworkNode) or isinstance(sock.node, CRVNCServerNode)):
			sock.Failure( SockWrapper.UNKNOWNSERVER, "You can't ask for server parameters without telling me what server you are!" )
			return
		if not sock.node.config.has_key( args ):
			sock.Failure( SockWrapper.UNKNOWNPARAM, "Server doesn't have param %s" % (args) )
			return
		#sock.Success( string.join( sock.node.config[args], " " ) )
		sock.Success( sock.node.config[args] )

	def do_fakerparam( self, sock, args ):
		"""do_fakerparam(sock, args)
		Sends the given app faker parameter."""
		if sock.node == None or not isinstance(sock.node,CRApplicationNode):
			sock.Failure( SockWrapper.UNKNOWNSERVER, "You can't ask for faker parameters without telling me what app faker you are!" )
			return
		if not sock.node.config.has_key( args ):
			sock.Failure( SockWrapper.UNKNOWNPARAM, "Faker doesn't have param %s" % (args) )
			return
		sock.Success( sock.node.config[args] )

	def do_servers( self, sock, args ):
		"""do_servers(sock, args)
		Returns list of servers attached to a (tilesort/pack) SPU."""
		if sock.SPUid == -1:
			sock.Failure( SockWrapper.UNKNOWNSPU, "You can't ask for servers without telling me what SPU id you are!" )
			return
		spu = allSPUs[sock.SPUid]
		if len(spu.servers) == 0:
			sock.Failure( SockWrapper.UNKNOWNPARAM, "SPU %d doesn't have servers!" % (sock.SPUid) )
			return

		servers = "%d " % len(spu.servers)
		for i in range(len(spu.servers)):
			(node, formatURL) = spu.servers[i]
			# The formatURL string may include a reference to the
			# resolved hostname. Replace it if it does.
			if node:
				host = node.host
				if node.dynamic_host:
					if dynamicHosts.has_key(host):
						host = dynamicHosts[host]
					else:
						sock.Failure( SockWrapper.UNKNOWNSERVER, "Server for dynamic host '%s' must be started before the appfaker" % (host) )
						return
				url = formatURL % {'host': QualifyHostname(host)}
#				url = formatURL % {'host': host}
			else:
				# probably a file: URL
				url = formatURL
			servers += "%s" % (url)
			if i != len(spu.servers) -1:
				servers += ','
		sock.Success( servers )

	def do_crutservers( self, sock, args ):
		if len(sock.node.crutservers) == 0:
			sock.Failure( SockWrapper.UNKNOWNPARAM, "CRUTClient %d doesn't have servers" % (sock.SPUid) )
			return

		crutservers = "%d " % len(sock.node.crutservers)
		for i in range(len(sock.node.crutservers)):
			(node,url) = sock.node.crutservers[i]
			crutservers+= "%s" % (url)
			if i != len(sock.node.crutservers) -1:
				crutservers += " "
		sock.Success( crutservers )

	def do_crutclients(self, sock, args ):
		#don't error here, you may not have any clients (e.g. last node in fan configuration)
		if len(sock.node.crutclients) == 0:
			sock.Success("0 CRUTserver doesn't have clients.")
			return

		crutclients = "%d " % len(sock.node.crutclients)
		for i in range(len(sock.node.crutclients)):
			(nocde,url) = sock.node.crutclients[i]
			crutclients += "%s" % (url)
			if i != len(sock.node.crutclients) -1:
				crutclients += " "
		sock.Success( crutclients )

	def do_serverids( self, sock, args ):
		"""do_serverids(sock, args)
		Sends the list of server IDs.
		XXX How is this different from do_servers? (ahern)
		"""
		# XXX this might only be temporary (BrianP)
		if sock.SPUid == -1:
			sock.Failure( SockWrapper.UNKNOWNSPU, "You can't ask for server ids without telling me what SPU id you are!" )
			return
		spu = allSPUs[sock.SPUid]
		if len(spu.servers) == 0:
			sock.Failure( SockWrapper.UNKNOWNPARAM, "SPU %d doesn't have servers!" % (sock.SPUid) )
			return

		servers = "%d " % len(spu.servers)
		for i in range(len(spu.servers)):
			(node, url) = spu.servers[i]
			if node == None:
				sock.Failure( SockWrapper.UNKNOWNSERVER, "Sorry, I don't know what SPU the server is running, you didn't tell me." )
				return
			servers += "%d" % (node.SPUs[0].ID)
			if i != len(spu.servers) - 1:
				servers += ' '
		sock.Success( servers )

	def do_tiles( self, sock, args ):
		"""do_tiles(sock, args)
		Returns the list of tiles associated with a SPU's Nth server."""
		# Note, an SPU asks for the tiles, but the tiles are really associated
		# with the servers that the (tilesort) SPU will talk to.  The arg to
		# this query indicates which server to return the tiles for.
		if sock.SPUid == -1:
			sock.Failure( SockWrapper.UNKNOWNSPU, "You can't ask for tiles without telling me what SPU id you are!" )
			return
		spu = allSPUs[sock.SPUid]
		if len(spu.servers) == 0:
			sock.Failure( SockWrapper.UNKNOWNPARAM, "SPU %d doesn't have servers!" % (sock.SPUid) )
			return
		server_num = int(args)
		if server_num < 0 or server_num >= len(spu.servers):
			sock.Failure( SockWrapper.UNKNOWNSERVER, "SPU %d doesn't have a server numbered %d" % (sock.SPUid, server_num) )
		(node, url) = spu.servers[server_num]
		if node == None:
			sock.Failure( SockWrapper.UNKNOWNSERVER, "No tiles for Null node")
			return
		self.tileReply( sock, node )

	def do_servertiles( self, sock, args ):
		"""do_servertiles(sock, args)
		Sends the defined tiles for a server."""
		if sock.node == None or not isinstance(sock.node,CRNetworkNode):
			sock.Failure( SockWrapper.UNKNOWNSERVER, "You can't ask for tiles without telling me what server you are!" )
			return
		self.tileReply( sock, sock.node )

	def do_server_param( self, sock, args ):
		"""Return a server parameter to the calling SPU."""
		if sock.SPUid == -1:
			sock.Failure( SockWrapper.UNKNOWNSPU, "You can't ask for SPU parameters without telling me what SPU id you are!" )
			return
		spu = allSPUs[sock.SPUid]
		args = string.split(args)
		server_num = int(args[0])
		param = args[1]
		if server_num < 0 or server_num >= len(spu.servers):
			sock.Failure( SockWrapper.UNKNOWNSERVER, "SPU %d doesn't have a server numbered %d" % (sock.SPUid, server_num) )
		(node, url) = spu.servers[server_num]
		if node.config.has_key(param):
			sock.Success( node.config[param] )
		else:
			sock.Success( "" )

	def tileReply( self, sock, node ):
		"""tileReply(sock, node)
		Packages up a tile message for socket communication.
		"""
		if len(node.tiles) == 0:
			sock.Failure( SockWrapper.UNKNOWNPARAM, "server doesn't have tiles!" )
			return
		tiles = "%d " % len(node.tiles)
		for i in range(len(node.tiles)):
			tile = node.tiles[i]  # tile is (x, y, w, h)
			tiles += "%d %d %d %d" % tile
			if i != len(node.tiles) - 1:
				tiles += ","
		sock.Success( tiles )

	def do_serverdisplaytiles( self, sock, args ):
		"""do_serverdisplaytiles(sock, args)
		Sends the defined tiles for a server."""
		if sock.node == None or not isinstance(sock.node,CRNetworkNode):
			sock.Failure( SockWrapper.UNKNOWNSERVER, "You can't ask for tiles without telling me what server you are!" )
			return
		self.displaytileReply( sock, sock.node )

	def displaytileReply( self, sock, node ):
		"""tileReply(sock, node)
		Packages up a tile message for socket communication.
		"""
		if len(node.tiles_on_displays) == 0:
			sock.Failure( SockWrapper.UNKNOWNPARAM, "server doesn't have tiles!" )
			return
		tiles = "%d " % len(node.tiles_on_displays)
		for i in range(len(node.tiles_on_displays)):
			tile = node.tiles_on_displays[i]
			tiles += "%d %d %d %d %d" % tile
			if i != len(node.tiles) - 1:
				tiles += ","
		sock.Success( tiles )

	def do_displays( self, sock, args ):
		"""do_displays(sock, args)
		Send the displays associated with a SPU"""
		n_displays = 0
		for spu in range(len(allSPUs)):
			n_displays += len(allSPUs[spu].displays)
		displays = "%d " % n_displays

		for spu in range(len(allSPUs)):
			for i in range(len(allSPUs[spu].displays)):
				display = allSPUs[spu].displays[i]
				
				tmp_display = "%d %d %d %s %s" % display

				reggie = re.compile('\]|\[|,')
				displays += "%s" % reggie.sub(' ', tmp_display)
				
				if i != len(allSPUs[spu].displays) - 1:
					displays += ","
		sock.Success( displays )
		
	def do_display_tiles( self, sock, args ):
		"""do_tiles(sock, args)
		Sends the defined tiles for a SPU."""
		if sock.SPUid == -1:
			sock.Failure( SockWrapper.UNKNOWNSPU, "You can't ask for tiles without telling me what SPU id you are!" )
			return
		spu = allSPUs[sock.SPUid]
		if len(spu.servers) == 0:
			sock.Failure( SockWrapper.UNKNOWNPARAM, "SPU %d doesn't have servers!" % (sock.SPUid) )
			return
		server_num = int(args)
		if server_num < 0 or server_num >= len(spu.servers):
			sock.Failure( SockWrapper.UNKNOWNSERVER, "SPU %d doesn't have a server numbered %d" % (sock.SPUid, server_num) )
		(node, url) = spu.servers[server_num]
		self.displayTileReply( sock, node )

	def displayTileReply( self, sock, node ):
		"""displayTileReply(sock, node)
		Packages up a tile message for socket communication.
		"""
		if len(node.tiles_on_displays) == 0:
			sock.Failure( SockWrapper.UNKNOWNPARAM, "server doesn't have display tiles!" )
			return
		tiles = "%d " % len(node.tiles_on_displays)
		for i in range(len(node.tiles_on_displays)):
			tile = node.tiles_on_displays[i]
			tiles += "%d %d %d %d %d" % tile
			if i != len(node.tiles_on_displays) - 1:
				tiles += ","
		sock.Success( tiles )

	def do_getvncclient( self, sock, args ):
		"""do_clients(sock, args)
		Like do_clients, return list of clients of this server, but this
		function is for vnc only.
		Note that the client/server terminology of Chromium (in this
		configuration anyway) is just the opposite of VNC's terminology."""
		# NOTE: we ignore args (the hostname)
		if sock.node == None or not isinstance(sock.node, CRVNCServerNode):
			sock.Failure( SockWrapper.UNKNOWNSERVER,
						  "You can't ask for vnc clients without telling " +
						  "me which VNC server node you are!" )
			return
		# Just find the replicate SPU
		for i in allSPUs.keys():
			spu = allSPUs[i]
			if spu.name == "replicate":
				sock.Success("1 tcpip %d" % spu.ID);
				return
		sock.Failure(SockWrapper.NOTHINGTOSAY,
					 "getvncclient: Didn't find VNC ApplicationNode and SPU")

	def do_clients( self, sock, args ):
		"""Returns a list of the clients who talk to this server.
		Example: '2 tcpip 4, ib 5' means there are two clients.  The first
		is SPU #4 using TCP/IP, the second is SPU #5 using Infiniband."""
		if sock.node == None or not isinstance(sock.node,CRNetworkNode):
			sock.Failure( SockWrapper.UNKNOWNSERVER, "You can't ask for clients without telling me what server you are!" )
			return
		total_clients = len(sock.node.clients) + len(sock.node.file_clients)
		clients = "%d " % total_clients
		for i in range(len(sock.node.clients)):
			(spu, protocol) = sock.node.clients[i]
			clients += "%s %d" % (protocol, spu.ID)
			if i != total_clients-1:
				clients += ','
		for i in range(len(sock.node.file_clients)):
			fname = sock.node.file_clients[i]
			clients += "%s %d" % (fname, -1)
			if i-len(sock.node.clients) != total_clients-1:
				clients += ','
		sock.Success( clients )
	
	def do_reset( self, sock, args ):
		"""do_reset(sock, args)
		Resets the mothership to its initial state."""
		for node in self.nodes:
			node.spokenfor = 0
			node.spusloaded = 0
			node.crut_spokenfor = 0
		# respawn auto-start nodes
		for cb in CR.startupCallbacks:
			cb(self)
		spawner = CRSpawner( self.nodes )
		spawner.start()
		sock.Success( "Server Reset" )

	def do_rank( self, sock, args ):
		"""do_rank( sock, args )
		Retrieves the node's rank and sends it on the socket (for Quadrics)."""
		if sock.node == None:
			sock.Failure( SockWrapper.UNKNOWNSERVER, "Identify yourself!" )
			return
		if not sock.node.config.has_key( 'rank' ):
			sock.Failure( SockWrapper.UNKNOWNPARAM, "Node didn't say what it's rank is." )
			return
		sock.Success( sock.node.config['rank'] )

	def do_disconnect( self, sock, args ):
		"""do_disconnect(sock, args)
		Disconnects from clients."""
		sock.Success( "Bye" )
		self.ClientDisconnect( sock )

	def do_logperf( self, sock, args ):
		"""do_logperf(sock, args)
		Logs Data to a logfile."""
		CROutput("%s" % args)
		sock.Success( "Dumped" )

	def do_gettilelayout( self, sock, args ):
		"""Call the user's tile layout function and return the resulting
		list of tiles."""
		if sock.SPUid == -1:
			sock.Failure( SockWrapper.UNKNOWNSPU,
							  "You can't ask for a new tile layout without "
							  "telling me what (tilesort) SPU id you are!" )
			return
		spu = allSPUs[sock.SPUid]
		if spu.name != "tilesort":
			# this is bad
			sock.Success("0")
			return
		argv = string.split(args)
		assert len(argv) == 2
		muralWidth = int(argv[0])
		muralHeight = int(argv[1])
		fn = getattr(spu, "layoutFunction" )
		if fn == None:
			# XXX return failure?
			sock.Success("0")
			return
		tiles = fn(muralWidth, muralHeight)
		# reformat the tiles list into a string
		result = str(len(tiles)) + " "
		for t in tiles:
			result += "%d %d %d %d %d, " % (t[0], t[1], t[2], t[3], t[4])
		if result[-2:] == ", ":
			result = result[:-2]  # remove trailing ", "
		assert len(result) < 8000  # see limit in getNewTiling in tilesort SPU
		sock.Success( result )
		return

	def do_getstatus( self, sock, args ):
		"""Returns status information for the mothership.
		
		The first argument determines what information is sent:
		0 [or nonexistent] - Send simple summary info back.
		1 - Send detailed summary info back.
		2 - Send node count.
		3 n attr - Send attr value for node n.
		
		# Not yet implemented, intended for GUI use
		4 [n] - Send node setup information for node n [if n not given, is sent for all nodes].
		5 [n] - Send node status information for node n [if n not given, is sent for all nodes].
		"""
		
		args = string.split(args)
		
		node_types = [ [CRNetworkNode, "network node"],
							[CRUTServerNode, "CRUT server node"],
							[CRUTProxyNode, "CRUT proxy node"],
							[CRApplicationNode, "application node"] ]
		
		TYPE, NAME, COUNT, CONNECTED = 0, 1, 2, 3
		
		result = ""
		
		if len(args) == 0 or (args[0] == "0" or args[0] == "1"):
			total_connected = 0
			
			# Set the node type count and node type connected counts to 0
			for node_type in node_types:
				node_type.append(0)
				node_type.append(0)
			
			for node in self.nodes:
				for node_type in node_types:
					if isinstance(node, node_type[TYPE]):
						node_type[COUNT] = node_type[COUNT] + 1
						if node.spokenfor:
							node_type[CONNECTED] = node_type[CONNECTED] + 1
							total_connected = total_connected + 1
			
			result = "%d nodes, %d connected" % (len(self.nodes), total_connected)
			
			is_detailed = (len(args) > 0 and args[0] == "1")
			
			for node_type in node_types:
				if node_type[COUNT]:
					if is_detailed:
						result = result + ("<br>  %sS:" % string.upper(node_type[NAME])) + self.__create_detailed_summary(node_type[TYPE])
					else:
						result = result + "<br>  %d %ss, %d connected" % (node_type[COUNT], node_type[NAME], node_type[CONNECTED])
			
		elif args[0] == "2":
			result = "%d" % len(self.nodes)
			
		elif args[0] == "3":
			if len(args) < 2:
				sock.Failure(SockWrapper.INVALIDPARAM, "getstatus usage: 3 n attr - Get attr value for node n.")
				return
			
			try:
				attr = getattr(self.nodes[int(args[1])], args[2])
			except AttributeError:
				sock.Failure(SockWrapper.INVALIDPARAM, "Invalid node attribute: %s" % args[2])
				return
			except IndexError:
				sock.Failure(SockWrapper.INVALIDPARAM, "Node index out of range: %s" % args[1])
				return
			except ValueError:
				sock.Failure(SockWrapper.INVALIDPARAM, "Invalid node index: %s" % args[1])
				return
			
			result = MakeString(attr)
		
		sock.Success( result )

	def __create_detailed_summary ( self, node_type ):
		"""Creates a detailed summary string."""
		
		result = ""
		
		for node_num in range(len(self.nodes)):
			node = self.nodes[node_num]
			if isinstance(node, node_type):
				if node.spokenfor:
					result = result + "<br>    %s[%d] has connected" % (node.host, node_num)
				else:
					result = result + "<br>    %s[%d] has NOT connected" % (node.host, node_num)
		
		return result

	def do_daughter( self, sock, args ):
		# This socket has identified itself as a daughter socket.  She
		# wants the node graph in reply; and in the future, she'll receive
		# propagated commands.
		self.daughters.append(sock)

		# Make a copy of the node graph; we'll munge the copy up
		# before sending it along.
		copyCR = copy.copy(self)

		# The daughter has no interest in any of our connections;
		# and the mothership has already autostarted everything
		copyCR.all_sockets = []
		copyCR.wrappers = {}
		copyCR.daughters = []
		copyCR.mother = None
		copyCR.enable_autostart = None

		# Package the copy of CR up with the other necessary globals
		globals = { }
		globals['cr'] = copyCR
		globals['allSPUs'] = allSPUs
		globals['dynamicHosts'] = dynamicHosts
		globals['dynamicHostsNeeded'] = dynamicHostsNeeded

		# Send them to the daughtership
		pickledGlobals = pickle.dumps(globals)
		# The current interface only sends one line at a time
		lines = pickledGlobals.splitlines()
		for line in lines:
			sock.MoreComing(line)
		sock.Success("hi sweetheart")

	def ProcessRequest( self, sock_wrapper ):
		"""ProcessRequest(sock_wrapper)
		Handles an incoming request, mapping it to an appropriate
		do_* function."""
		try:
			line = sock_wrapper.readline()
			CRDebug("Processing mothership request: \"%s\"" % line)
		except:
			# Client is gone.  Make sure it isn't a special client
			if sock_wrapper in self.daughters:
				CRDebug("Daughter quit without saying goodbye?  How rude!")
				self.daughters.remove(sock_wrapper)
				self.ClientDisconnect( sock_wrapper )
				return
			elif sock_wrapper == self.mother:
				Fatal("Mother is gone; so am I.")
			else:
				CRDebug( "Client quit without saying goodbye?  How rude!" )
				self.ClientDisconnect( sock_wrapper )
				return

		words = string.split( line )
		if len(words) == 0:
			self.ClientError( sock_wrapper,
							  SockWrapper.NOTHINGTOSAY, "Request was empty?" )
			#sock_wrapper.Failure( SockWrapper.NOTHINGTOSAY, "Request was empty?" )
			return
		command = string.lower( words[0] )
		arguments = string.join( words[1:] )

		try:
			fn = getattr(self, 'do_%s' % command )
		except AttributeError:
			sock_wrapper.Failure( SockWrapper.UNKNOWNCOMMAND, "Unknown command: %s" % command )
			return

		fn( sock_wrapper, arguments)

class CRDaughtership:
	def __init__( self, mother = None ):
		self.mother = None
		self.cr = None

		# Poor little lost daughtership, looking for her mother
		if mother == None:
			if os.environ.has_key('CRMOTHERSHIP'):
				mother = os.environ['CRMOTHERSHIP']
		if mother == None:
			CRInfo("I lost my mother - using localhost on default port")
			motherHost = 'localhost'
			motherPort = DefaultMothershipPort
		else:
			colon = string.find(mother, ':')
			if colon >= 0:
				motherHost = mother[0:colon-1]
				try:
					motherPort = int(mother[colon+1:])
				except:
					CRInfo("Illegal port number %s, using default" % mother[colon+1:])
					motherPort = DefaultMothershipPort
			else:
				motherHost = mother
				motherPort = DefaultMothershipPort

		# When we start the daughtership as a surrogate mothership, it will
		# read the CRMOTHERSHIP variable to configure itself.  Make sure it
		# reads the CRDAUGHTERSHIP variable instead, by munging the environment.
		if os.environ.has_key('CRDAUGHTERSHIP'):
			os.environ['CRMOTHERSHIP'] = os.environ['CRDAUGHTERSHIP']
		else:
			os.environ['CRMOTHERSHIP'] = ':10001'
				
		# Try all available socket types to reach our mothership
		motherSocket = None
		for res in socket.getaddrinfo(motherHost, motherPort, socket.AF_UNSPEC, socket.SOCK_STREAM, 0, socket.AI_PASSIVE):
			(af, socktype, proto, canonname, sa) = res

			try:
				motherSocket = socket.socket( af, socktype, proto )
			except:
				CRDebug( "Couldn't create socket of family %u, trying another one" % af )
				motherSocket = None
				continue

			try:
				motherSocket.connect( sa )
			except:
				sa.close()
				CRDebug( "Couldn't connect to mothership at %s:%d" % (motherHost, motherPort))
				motherSocket = None
				continue

		if motherSocket == None:
			Fatal("Could not open connection to mothership at %s:%d" % (motherHost, motherPort))

		self.mother = SockWrapper(motherSocket)
		# Tell the mothership that we are a daughtership, so that we'll
		# receive propagated commands.
		self.mother.Send("daughter")

		# The response will come in multiple lines
		done = False
		pickledGlobals = ""
		while not done:
			reply = self.mother.readline()
			words = string.split(reply, None, 1)
			if len(words) == 0:
				Fatal("Mothership returned empty reply?")
			if words[0] == "200":
				# Done
				done = 1
			elif words[0] == "100":
				# More coming
				pickledGlobals = pickledGlobals + words[1] + "\n"
			else:
				Fatal("Mothership doesn't recognize its daughter [%s]" % words[0])
		
		# By now we've got the whole pickle.  See if we can unpickle it.
		try:
			globals = pickle.loads(pickledGlobals)
		except:
			Fatal("Could not unpickle Cr globals")

		# Unpack all the globals that we were given
		try:
			global allSPUs, dynamicHosts, dynamicHostsNeeded
			self.cr = globals['cr']
			allSPUs = globals['allSPUs']
			dynamicHosts = globals['dynamicHosts']
			dynamicHostsNeeded = globals['dynamicHostsNeeded']
		except KeyError, badKey:
			Fatal("Globals were missing the key '%s'" % badKey)
				
		# Modify the CR configuration so it knows it has a mother.
		# Some commands will then automatically propagate to the
		# mothership from us.
		self.cr.mother = self.mother

		# The mothership should already have taken care of eliminating
		# other things we don't want to see (like the mothership's own
		# sockets, etc.), so we should be ready to go.

	def Go(self):
		# Just tell the Chromium configuration to go.  It should be
		# all set up and ready.
		self.cr.Go()





def CreateDaemon(Logfile=None):
	"""Detach this process from the controlling terminal and run it in the
	background as a daemon.
	----------------------------------------------------------------------
	This code found at:
	http://aspn.activestate.com/ASPN/Cookbook/Python/Recipe/278731

	Author: Chad J. Schroeder
	Copyright (C) 2005 Chad J. Schroeder
	"""

	# Default daemon parameters.
	# File mode creation mask of the daemon.
	UMASK = 0

	# Default working directory for the daemon.
	WORKDIR = "/"

	# Default maximum for the number of available file descriptors.
	MAXFD = 1024

	# The standard I/O file descriptors are redirected to /dev/null by default,
	# or to the specified file if a logfile was specified.
	if (hasattr(os, "devnull")):
		DEVNULL = os.devnull
	else:
		DEVNULL = "/dev/null"

	INPUT_FROM = DEVNULL
	if Logfile == None:
		OUTPUT_TO = DEVNULL
	else:
		OUTPUT_TO = Logfile

	try:
		# Fork a child process so the parent can exit.  This returns control to
		# the command-line or shell.  It also guarantees that the child will not
		# be a process group leader, since the child receives a new process ID
		# and inherits the parent's process group ID.  This step is required
		# to insure that the next call to os.setsid is successful.
		pid = os.fork()
	except OSError, e:
		raise Exception, "%s [%d]" % (e.strerror, e.errno)

	if (pid == 0):	# The first child.
		# To become the session leader of this new session and the process group
		# leader of the new process group, we call os.setsid().  The process is
		# also guaranteed not to have a controlling terminal.
		os.setsid()

		# Is ignoring SIGHUP necessary?
		#
		# It's often suggested that the SIGHUP signal should be ignored before
		# the second fork to avoid premature termination of the process.  The
		# reason is that when the first child terminates, all processes, e.g.
		# the second child, in the orphaned group will be sent a SIGHUP.
		#
		# "However, as part of the session management system, there are exactly
		# two cases where SIGHUP is sent on the death of a process:
		#
		#	1) When the process that dies is the session leader of a session that
		#		is attached to a terminal device, SIGHUP is sent to all processes
		#		in the foreground process group of that terminal device.
		#	2) When the death of a process causes a process group to become
		#		orphaned, and one or more processes in the orphaned group are
		#		stopped, then SIGHUP and SIGCONT are sent to all members of the
		#		orphaned group." [2]
		#
		# The first case can be ignored since the child is guaranteed not to have
		# a controlling terminal.  The second case isn't so easy to dismiss.
		# The process group is orphaned when the first child terminates and
		# POSIX.1 requires that every STOPPED process in an orphaned process
		# group be sent a SIGHUP signal followed by a SIGCONT signal.  Since the
		# second child is not STOPPED though, we can safely forego ignoring the
		# SIGHUP signal.  In any case, there are no ill-effects if it is ignored.
		#
		# import signal			  # Set handlers for asynchronous events.
		# signal.signal(signal.SIGHUP, signal.SIG_IGN)

		try:
			# Fork a second child and exit immediately to prevent zombies.  This
			# causes the second child process to be orphaned, making the init
			# process responsible for its cleanup.  And, since the first child is
			# a session leader without a controlling terminal, it's possible for
			# it to acquire one by opening a terminal in the future (System V-
			# based systems).  This second fork guarantees that the child is no
			# longer a session leader, preventing the daemon from ever acquiring
			# a controlling terminal.
			pid = os.fork()	# Fork a second child.
		except OSError, e:
			raise Exception, "%s [%d]" % (e.strerror, e.errno)

		if (pid == 0):	# The second child.
			# Since the current working directory may be a mounted filesystem, we
			# avoid the issue of not being able to unmount the filesystem at
			# shutdown time by changing it to the root directory.
			os.chdir(WORKDIR)
			# We probably don't want the file mode creation mask inherited from
			# the parent, so we give the child complete control over permissions.
			os.umask(UMASK)
		else:
			# exit() or _exit()?  See below.
			os._exit(0)	# Exit parent (the first child) of the second child.
	else:
		# exit() or _exit()?
		# _exit is like exit(), but it doesn't call any functions registered
		# with atexit (and on_exit) or any registered signal handlers.  It also
		# closes any open file descriptors.  Using exit() may cause all stdio
		# streams to be flushed twice and any temporary files may be unexpectedly
		# removed.  It's therefore recommended that child branches of a fork()
		# and the parent branch(es) of a daemon use _exit().
		os._exit(0)	# Exit parent of the first child.

	# Close all open file descriptors.  This prevents the child from keeping
	# open any file descriptors inherited from the parent.  There is a variety
	# of methods to accomplish this task.  Three are listed below.
	#
	# Try the system configuration variable, SC_OPEN_MAX, to obtain the maximum
	# number of open file descriptors to close.  If it doesn't exists, use
	# the default value (configurable).
	#
	# try:
	#	 maxfd = os.sysconf("SC_OPEN_MAX")
	# except (AttributeError, ValueError):
	#	 maxfd = MAXFD
	#
	# OR
	#
	# if (os.sysconf_names.has_key("SC_OPEN_MAX")):
	#	 maxfd = os.sysconf("SC_OPEN_MAX")
	# else:
	#	 maxfd = MAXFD
	#
	# OR
	#
	# Use the getrlimit method to retrieve the maximum file descriptor number
	# that can be opened by this process.  If there is not limit on the
	# resource, use the default value.
	#
	import resource		# Resource usage information.
	maxfd = resource.getrlimit(resource.RLIMIT_NOFILE)[1]
	# On old versions of Python, which don't have resource.RLIM_INFINITY,
	# default to MAXFD.
	try:
		if (maxfd == resource.RLIM_INFINITY):
			maxfd = MAXFD
	except:
		maxfd = MAXFD
  
	# Iterate through and close all file descriptors.
	for fd in range(0, maxfd):
		try:
			os.close(fd)
		except OSError:	# ERROR, fd wasn't open to begin with (ignored)
			pass

	# Redirect the standard I/O file descriptors to the specified file.  Since
	# the daemon has no controlling terminal, most daemons redirect stdin,
	# stdout, and stderr to /dev/null.  This is done to prevent side-effects
	# from reads and writes to the standard I/O file descriptors.

	# This call to open is guaranteed to return the lowest file descriptor,
	# which will be 0 (stdin), since it was closed above.
	os.open(INPUT_FROM, os.O_RDONLY)	# standard input (0)
	os.open(OUTPUT_TO, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0600)	# standard output (1)
	os.dup2(1, 2)				# standard error (2)

	return(0)
