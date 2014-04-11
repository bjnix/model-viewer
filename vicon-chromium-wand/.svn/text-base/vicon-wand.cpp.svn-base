#include "Client.h"

#include <GL/glew.h>
#include <GL/glut.h>

#include <unistd.h>
#include <iostream>
#include <fstream>
#include <cassert>
#include <ctime>

#ifdef WIN32
#include <conio.h>   // For _kbhit()
#include <cstdio>   // For getchar()
#include <windows.h> // For Sleep()
#endif // WIN32

#include <time.h>

using namespace ViconDataStreamSDK::CPP;

// Make a new vicon client
Client MyClient;
std::string HostName = "141.219.28.107:801";
//std::string HostName = "localhost:801";

// screen width/height indicate the size of the window on our screen (not the size of the display wall). The aspect ratio must match the actual display wall.
const GLdouble SCREEN_WIDTH = (1920*6)/8.0;  
const GLdouble SCREEN_HEIGHT = (1080.0*4)/8.0;
const float screenAspectRatio = SCREEN_WIDTH/SCREEN_HEIGHT;



namespace
{
	std::string Adapt( const bool i_Value )
	{
		return i_Value ? "True" : "False";
	}

	std::string Adapt( const Direction::Enum i_Direction )
	{
		switch( i_Direction )
		{
			case Direction::Forward:
				return "Forward";
			case Direction::Backward:
				return "Backward";
			case Direction::Left:
				return "Left";
			case Direction::Right:
				return "Right";
			case Direction::Up:
				return "Up";
			case Direction::Down:
				return "Down";
			default:
				return "Unknown";
		}
	}

	std::string Adapt( const DeviceType::Enum i_DeviceType )
	{
		switch( i_DeviceType )
		{
			case DeviceType::ForcePlate:
				return "ForcePlate";
			case DeviceType::Unknown:
			default:
				return "Unknown";
		}
	}

	std::string Adapt( const Unit::Enum i_Unit )
	{
		switch( i_Unit )
		{
			case Unit::Meter:
				return "Meter";
			case Unit::Volt:
				return "Volt";
			case Unit::NewtonMeter:
				return "NewtonMeter";
			case Unit::Newton:
				return "Newton";
			case Unit::Kilogram:
				return "Kilogram";
			case Unit::Second:
				return "Second";
			case Unit::Ampere:
				return "Ampere";
			case Unit::Kelvin:
				return "Kelvin";
			case Unit::Mole:
				return "Mole";
			case Unit::Candela:
				return "Candela";
			case Unit::Radian:
				return "Radian";
			case Unit::Steradian:
				return "Steradian";
			case Unit::MeterSquared:
				return "MeterSquared";
			case Unit::MeterCubed:
				return "MeterCubed";
			case Unit::MeterPerSecond:
				return "MeterPerSecond";
			case Unit::MeterPerSecondSquared:
				return "MeterPerSecondSquared";
			case Unit::RadianPerSecond:
				return "RadianPerSecond";
			case Unit::RadianPerSecondSquared:
				return "RadianPerSecondSquared";
			case Unit::Hertz:
				return "Hertz";
			case Unit::Joule:
				return "Joule";
			case Unit::Watt:
				return "Watt";
			case Unit::Pascal:
				return "Pascal";
			case Unit::Lumen:
				return "Lumen";
			case Unit::Lux:
				return "Lux";
			case Unit::Coulomb:
				return "Coulomb";
			case Unit::Ohm:
				return "Ohm";
			case Unit::Farad:
				return "Farad";
			case Unit::Weber:
				return "Weber";
			case Unit::Tesla:
				return "Tesla";
			case Unit::Henry:
				return "Henry";
			case Unit::Siemens:
				return "Siemens";
			case Unit::Becquerel:
				return "Becquerel";
			case Unit::Gray:
				return "Gray";
			case Unit::Sievert:
				return "Sievert";
			case Unit::Katal:
				return "Katal";

			case Unit::Unknown:
			default:
				return "Unknown";
		}
	}
}



void viconExit()
{
    MyClient.DisableSegmentData();
//    MyClient.DisableMarkerData();
//    MyClient.DisableUnlabeledMarkerData();
//    MyClient.DisableDeviceData();

	// TODO: Disconnect seems to cause a hang. -Scott Kuhl
    // Disconnect and dispose
    int t = clock();
    std::cout << " Disconnecting..." << std::endl;
    MyClient.Disconnect();
    int dt = clock() - t;
    double secs = (double) (dt)/(double)CLOCKS_PER_SEC;
    std::cout << " Disconnect time = " << secs << " secs" << std::endl;
}

void viconInit()
{
    // Connect to a server
    std::cout << "Connecting to " << HostName << " ..." << std::flush;
	int attemptConnectCount = 0;
	const int MAX_CONNECT_ATTEMPTS=2;
    while( !MyClient.IsConnected().Connected && attemptConnectCount < MAX_CONNECT_ATTEMPTS)
    {
		attemptConnectCount++;
		bool ok = false;
		ok =( MyClient.Connect( HostName ).Result == Result::Success );
		if(!ok)
			std::cout << "Warning - connect failed..." << std::endl;
		std::cout << ".";
		sleep(1);
    }
	if(attemptConnectCount == MAX_CONNECT_ATTEMPTS)
	{
		printf("Giving up making connection to Vicon system\n");
		return;
	}
    std::cout << std::endl;

    // Enable some different data types
    MyClient.EnableSegmentData();
    //MyClient.EnableMarkerData();
    //MyClient.EnableUnlabeledMarkerData();
    //MyClient.EnableDeviceData();

    std::cout << "Segment Data Enabled: "          << Adapt( MyClient.IsSegmentDataEnabled().Enabled )         << std::endl;
    std::cout << "Marker Data Enabled: "           << Adapt( MyClient.IsMarkerDataEnabled().Enabled )          << std::endl;
    std::cout << "Unlabeled Marker Data Enabled: " << Adapt( MyClient.IsUnlabeledMarkerDataEnabled().Enabled ) << std::endl;
    std::cout << "Device Data Enabled: "           << Adapt( MyClient.IsDeviceDataEnabled().Enabled )          << std::endl;

    // Set the streaming mode
    //MyClient.SetStreamMode( ViconDataStreamSDK::CPP::StreamMode::ClientPull );
    // MyClient.SetStreamMode( ViconDataStreamSDK::CPP::StreamMode::ClientPullPreFetch );
    MyClient.SetStreamMode( ViconDataStreamSDK::CPP::StreamMode::ServerPush );

    // Set the global up axis
    MyClient.SetAxisMapping( Direction::Forward, 
                             Direction::Left, 
                             Direction::Up ); // Z-up
    // MyClient.SetGlobalUpAxis( Direction::Forward, 
    //                           Direction::Up, 
    //                           Direction::Right ); // Y-up

    Output_GetAxisMapping _Output_GetAxisMapping = MyClient.GetAxisMapping();
    std::cout << "Axis Mapping: X-" << Adapt( _Output_GetAxisMapping.XAxis ) 
			  << " Y-" << Adapt( _Output_GetAxisMapping.YAxis ) 
			  << " Z-" << Adapt( _Output_GetAxisMapping.ZAxis ) << std::endl;

    // Discover the version number
    Output_GetVersion _Output_GetVersion = MyClient.GetVersion();
    std::cout << "Version: " << _Output_GetVersion.Major << "." 
			  << _Output_GetVersion.Minor << "." 
			  << _Output_GetVersion.Point << std::endl;

}



// an atexit() callback:
void exitCallback()
{
	viconExit();
	return;
}

void keyboard(unsigned char key, int x, int y)
{
	if (key == 27 || key == 'q')  // escape key, exit program
		exit(0);
}

void addBox(char *ViconString, float red, float green, float blue, float size)
{
  glPushMatrix();

	// units are in millimeters, lets switch to meters
	Output_GetSegmentGlobalTranslation globalTranslate = MyClient.GetSegmentGlobalTranslation(ViconString, ViconString);
	glTranslatef(globalTranslate.Translation[ 0 ] / 1000,
		     globalTranslate.Translation[ 1 ] / 1000,
		     globalTranslate.Translation[ 2 ] / 1000);
	// Get the global segment rotation as a matrix

	// Vicon rotation matrix is in row-major order and OpenGL expects column-major order.
	Output_GetSegmentGlobalRotationMatrix globalRotMatrix = MyClient.GetSegmentGlobalRotationMatrix(ViconString, ViconString);
	double transposedMatrix[16] = { globalRotMatrix.Rotation[ 0 ],
					globalRotMatrix.Rotation[ 3 ],
					globalRotMatrix.Rotation[ 6 ],
					0, // end of column 1
					globalRotMatrix.Rotation[ 1 ],
					globalRotMatrix.Rotation[ 4 ],
					globalRotMatrix.Rotation[ 7 ],
					0, // end of column 2
					globalRotMatrix.Rotation[ 2 ],
					globalRotMatrix.Rotation[ 5 ],
					globalRotMatrix.Rotation[ 8 ],
					0, // end of column 3
					0, 0, 0, 1 }; // end of column 4

	glMultMatrixd(transposedMatrix);

	// cube is drawn centered at the origin, move it up so that it
	// is sitting on the xy plane.
	glTranslatef(0, 0, size/2.0);
	glColor3f(red, green, blue);
	glutSolidCube(size); // 1x1x1 cube.

	glPopMatrix();
}


void display()
{
	glEnable(GL_LIGHTING) ;
	glEnable(GL_LIGHT0);
	glEnable(GL_COLOR_MATERIAL);
	glEnable(GL_NORMALIZE);
	glEnable(GL_DEPTH_TEST);
	
	glClearColor(0,0,0,0);
	glClear(GL_COLOR_BUFFER_BIT|GL_DEPTH_BUFFER_BIT);
	glColor3f(1,1,1);

	// Get a frame
	if(MyClient.GetFrame().Result != Result::Success )
		printf("WARNING: Inside display() and there is no data from Vicon...\n");

	glMatrixMode(GL_PROJECTION);
	glLoadIdentity();

#if 0
	gluPerspective(45, screenAspectRatio, .1, 30);

	glMatrixMode(GL_MODELVIEW);
	glLoadIdentity();
	gluLookAt(0,4,1.67,
		  0,0,1.67,
		  0,0,1);

#else
	Output_GetSegmentGlobalTranslation globalTranslateDude = MyClient.GetSegmentGlobalTranslation( "Dude", "Dude");
	float x = globalTranslateDude.Translation[ 0 ] / 1000;
	float y = globalTranslateDude.Translation[ 1 ] / 1000;
	float z = globalTranslateDude.Translation[ 2 ] / 1000;

	// bottom of screen z=11 inches or .28 meters
	// top of screen z~8.5 feet or 2.6 meters
	// 1 screen width 40.5 inches or 1.03 meters
	// screen y=3.327 meters - y is the distance to the screen from origin
	// the x axis runs horizontally (negative x on the left-hand side of screen)
	// -.4 is to adjust for the fact that the screens aren't centered in front of origin.
	float left   = -1.03*3-x;
	float right  =  1.03*3-x;
	float bottom =  0.28-z;
	float top    =  2.60-z;
	float near   =  3.9-y;
	float far    =  30;
	//	printf("%f %f %f %f %f %f\n", left, right, bottom, top, near, far);
	
	glFrustum(left, right, bottom, top, near, far);
	//gluPerspective(45, screenAspectRatio, .1, 30);

	glMatrixMode(GL_MODELVIEW);
	glLoadIdentity();
	gluLookAt(-x, -y, z,
		  -x, -y-1,   z,
		   0,  0,   1);
#endif

	glTranslatef(0,-8,0);
	  
	// Draw a 1x1 meter square at origin.
	glPointSize(10);
	glBegin(GL_POINTS);
	for(float i=-.5; i<.5; i=i+.1)
		for(float j=-.5; j<.5; j=j+.1)
			glVertex3f(i,j,0);
	glEnd();

	glScalef(-1,1,1); // make rotations of box act like a mirror

	addBox("Wand", 1,1,1, 1);
	addBox("HandR", .9,.2,.1, .3);
	addBox("HandOR", .1,1,.2, .3);
	addBox("FootL", .2,.1,.95, .3);
	addBox("FootOL", .4,.8,.9, .3);
	



	glFlush();
	glutSwapBuffers();
	glutPostRedisplay(); // call display() repeatedly
}


int main( int argc, char* argv[] )
{
	glutInit(&argc, argv); //initialize the toolkit
	glutInitDisplayMode(GLUT_DOUBLE | GLUT_RGB | GLUT_DEPTH);  //set display mode
	glutInitWindowSize(SCREEN_WIDTH, SCREEN_HEIGHT); //set window size
	glutInitWindowPosition(0, 0); //set window position on screen
	glutCreateWindow(argv[0]); //open the screen window

	int glew_err = glewInit();
	if(glew_err != GLEW_OK)
		fprintf(stderr, "GLEW Error: %s\n", glewGetErrorString(glew_err));

	glutDisplayFunc(display);
	glutKeyboardFunc(keyboard);

	atexit(exitCallback);
	viconInit();

	glutMainLoop();

}
