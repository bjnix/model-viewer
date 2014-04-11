BIN=importer

# CCSR: glew is installed in /usr/local/glew/VERSION
# IVS: glew is installed in /opt/viz/
GLEW_INC=
GLEW_LIBS=-lGLEW


#VICON_LIBS=-L./vicon-libs -Wl,-rpath,./vicon-libs -lViconDataStreamSDK_CPP
BASIC_LIBS=-lGL -lstdc++ -lc -lm -lglut -lGLU

ALL_LIBS=${GLEW_LIBS} ${VICON_LIBS} ${BASIC_LIBS}
ALL_INC=${GLEW_INC}

${BIN}: l3dassimpimport.cpp textfile.cpp
	g++ -g -Wall -o ${BIN} ${ALL_INC} $^ ${ALL_LIBS}

clean:
	rm ${BIN} 
