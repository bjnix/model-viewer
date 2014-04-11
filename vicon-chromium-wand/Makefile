BIN=vicon-wand

# CCSR: glew is installed in /usr/local/glew/VERSION
# IVS: glew is installed in /opt/viz/
GLEW_INC=
GLEW_LIBS=-lGLEW

ifeq "$(HOSTNAME)" "ivs.research.mtu.edu"
GLEW_INC=-I/opt/viz/include
GLEW_LIBS=-Wl,-rpath,/opt/viz/lib -L/opt/viz/lib -lGLEW
endif

ifeq "$(HOSTNAME)" "ccsr.ee.mtu.edu"
GLEW_INC=-I/usr/local/glew/1.9.0/include
GLEW_LIBS=-Wl,-rpath,/usr/local/glew/1.9.0/lib -L/usr/local/glew/1.9.0/lib -lGLEW
endif

VICON_LIBS=-L../vicon-libs -Wl,-rpath,../vicon-libs -lViconDataStreamSDK_CPP
BASIC_LIBS=-lGL -lstdc++ -lc -lm -lglut -lGLU

ALL_LIBS=${GLEW_LIBS} ${VICON_LIBS} ${BASIC_LIBS}
ALL_INC=${GLEW_INC}

${BIN}: Client.h vicon-wand.cpp Makefile
	g++ -g -Wall -o ${BIN} ${ALL_INC} vicon-wand.cpp ${ALL_LIBS}

clean:
	rm ${BIN} 
