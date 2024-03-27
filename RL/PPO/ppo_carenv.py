import carla
import gym
from gym import spaces
import numpy as np
import cv2
import math
import time
import random
from collections import deque


SECONDS_PER_EPISODE = 60

N_CHANNELS = 3
HEIGHT = 240
WIDTH = 320

FIXED_DELTA_SECONDS = 0.2

SPIN = 8

HEIGHT_REQUIRED_PORTION = 0.5 #bottom share, e.g. 0.1 is take lowest 10% of rows
WIDTH_REQUIRED_PORTION = 0.9

SHOW_PREVIEW = True

class CarlaEnv(gym.Env):
    SHOW_CAM = SHOW_PREVIEW
    STEER_AMT = 1.0
    im_width = WIDTH
    im_height = HEIGHT
    front_camera = None
    CAMERA_POS_Z = 1.3 
    CAMERA_POS_X = 1.4
    PREFERRED_SPEED = 10
    SPEED_THRESHOLD = 2 #defines when we get close to desired speed so we drop the
    KMH_BUFFER_LEN = 15 #number of recent timesteps to calculate average speed over



    #Custom Environment that follows gym interface
    def __init__(self, host='localhost', port=2000):
            # Define action and observation space
            # They must be gym.spaces objects
            #descrete actions
        self.action_space = spaces.MultiDiscrete([9,9,4])
        # 9 discrete for 9 different steering angles
        #4 for throttle/braking
        self.kmh_buffer = []

        self.height_from = int(HEIGHT * (1 -HEIGHT_REQUIRED_PORTION))
        self.width_from = int((WIDTH - WIDTH * WIDTH_REQUIRED_PORTION) / 2)
        self.width_to = self.width_from + int(WIDTH_REQUIRED_PORTION * WIDTH)
        self.new_height = HEIGHT - self.height_from
        self.new_width = self.width_to - self.width_from
        self.image_for_CNN = None
            
        self.observation_space = spaces.Box(low=0, high=1.0,
                                            shape=(HEIGHT, WIDTH, N_CHANNELS), dtype=np.uint8)
                
        self.client = carla.Client(host, port)
        self.client.set_timeout(40.0)
        self.world = self.client.get_world()
        
            
        self.settings = self.world.get_settings()
        self.settings.no_rendering_mode = True
        self.settings.synchronous_mode = False
        self.settings.fixed_delta_seconds = FIXED_DELTA_SECONDS
        self.world.apply_settings(self.settings)
        self.blueprint_library = self.world.get_blueprint_library()
        self.model_3 = self.blueprint_library.filter("model3")[0]

        

    def cleanup(self):

        for sensor in self.world.get_actors().filter('*sensor*'):
            sensor.destroy()
        for actor in self.world.get_actors().filter('*vehicle*'):
            actor.destroy()
        cv2.destroyAllWindows()


    def step(self, action):
        self.step_counter +=1

        
        # Steering, throttle, and brake mappings based on action space indices
        steer_values = [-0.9, -0.25, -0.1, -0.05, 0.0, 0.05, 0.1, 0.25, 0.9]
        throttle_values = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.65, 0.8, 1.0]
        brake_values = [0.0, 0.33, 0.66, 1.0]  # Hypothetical brake values for illustration

        # Assuming 'action' is received from the environment, representing indices for each control
        steer_index, throttle_index, brake_index = action

        # Assign the corresponding values based on the action indices
        steer = steer_values[steer_index]
        throttle = throttle_values[throttle_index]
        brake = brake_values[brake_index]


        #prints inputs every 30 steps
        if self.step_counter % 30 == 0:
            print('Steer input from model:',steer,',throttle: ',throttle)

        self.vehicle.apply_control(carla.VehicleControl(throttle=throttle, steer=steer, brake=brake))   

       

        distance_travelled = self.initial_location.distance(self.vehicle.get_location())

        #creates camera
        cam = self.front_camera

        if self.SHOW_CAM:
            cv2.imshow('Camera', cam)
            cv2.waitKey(1)

        #prevents the vehicle from chasing its tail
        lock_duration = 0
        if self.steering_lock == False:
            if steer<-0.6 or steer>0.6:
                self.steering_lock = True
                self.steering_lock_start = time.time()
        else:
            if steer<-0.6 or steer>0.6:
                lock_duration = time.time() - self.steering_lock_start

        #rewards 
        reward = 0
        done = False
        #punish for collision
        if len(self.collision_hist) != 0:
            done = True
            reward = reward - 300
            self.cleanup()
        if len(self.lane_invade_hist) != 0:
            # done = True
            reward = reward - 200
            # self.cleanup()

        #lock ups
        if lock_duration > 3:
            reward = reward - 150
            done = True
            self.cleanup()
        elif lock_duration > 1:
            reward = reward - 25

        #gets velocity
        v = self.vehicle.get_velocity()
        #calculates km/h
        kmh = int(3.6 *math.sqrt(v.x**2 +v.y**2 +v.z**2))
        self.kmh_buffer.append(kmh)
		#calculate average speed over the last timsteps steps
        avg_kmh = sum(self.kmh_buffer) / len(self.kmh_buffer)
    
        #acceleration
        if avg_kmh < 0.1:
            reward = reward - 10
        elif avg_kmh < 1:
            reward = reward - 5
        elif avg_kmh < 5:
            reward = reward - 2
        elif avg_kmh <= 15:
            reward = reward - 1
        elif avg_kmh > 15 and avg_kmh< 40:
            reward = reward + 3
        elif avg_kmh > 60:
            reward = reward - 5
                  

        if distance_travelled < 30:
            reward = reward - 1
        elif distance_travelled < 50:
            reward = reward + 1
        else:
            reward = reward + 2
        #checks for episode duration
        if self.episode_start + SECONDS_PER_EPISODE < time.time():
            done = True
            self.cleanup()
        return cam/255.0, reward, done, {}	#curly brackets - empty dictionary required by SB3 format

    #reset everything back to normal
    def reset(self):
        self.collision_hist = []
        self.actor_list = []
        self.lane_invade_hist = []
        self.kmh_buffer = deque(maxlen=self.KMH_BUFFER_LEN)
        self.transform = random.choice(self.world.get_map().get_spawn_points())
		
        self.vehicle = None
        while self.vehicle is None:
            try:
        # connect
                self.vehicle = self.world.spawn_actor(self.model_3, self.transform)
            except:
                pass
        self.actor_list.append(self.vehicle)
        self.initial_location = self.vehicle.get_location()
        self.sem_cam = self.blueprint_library.find('sensor.camera.semantic_segmentation')
        self.sem_cam.set_attribute("image_size_x", f"{self.im_width}")
        self.sem_cam.set_attribute("image_size_y", f"{self.im_height}")
        self.sem_cam.set_attribute("fov", f"90")

		
        camera_init_trans = carla.Transform(carla.Location(z=self.CAMERA_POS_Z,x=self.CAMERA_POS_X))
        self.sensor = self.world.spawn_actor(self.sem_cam, camera_init_trans, attach_to=self.vehicle)
        self.actor_list.append(self.sensor)
        self.sensor.listen(lambda data: self.process_img(data))

        self.vehicle.apply_control(carla.VehicleControl(throttle=0.0, brake=0.0))
        time.sleep(3)

        angle_adj = random.randrange(-SPIN, SPIN, 1)
        trans = self.vehicle.get_transform()
        trans.rotation.yaw = trans.rotation.yaw + angle_adj
        self.vehicle.set_transform(trans)

		# showing camera at the spawn point
        if self.SHOW_CAM:
            cv2.namedWindow('Sem Camera',cv2.WINDOW_AUTOSIZE)
            cv2.imshow('Sem Camera', self.front_camera)
            cv2.waitKey(1)
        
        colsensor = self.blueprint_library.find("sensor.other.collision")
        self.colsensor = self.world.spawn_actor(colsensor, camera_init_trans, attach_to=self.vehicle)
        self.actor_list.append(self.colsensor)
        self.colsensor.listen(lambda event: self.collision_data(event))

        lanesensor = self.blueprint_library.find("sensor.other.lane_invasion")
        self.lanesensor = self.world.spawn_actor(lanesensor, camera_init_trans, attach_to=self.vehicle)
        self.actor_list.append(self.lanesensor)
        self.lanesensor.listen(lambda event: self.lane_data(event))

        while self.front_camera is None:
            time.sleep(0.01)
		
        self.episode_start = time.time()
        self.steering_lock = False
        self.steering_lock_start = None # this is to count time in steering lock and start penalising for long time in steering lock
        self.step_counter = 0
        self.vehicle.apply_control(carla.VehicleControl(throttle=0.0, brake=0.0))

        return self.front_camera/255.0


    def process_img(self, image):
        #converts image to cityscapespallete for semantic segmenatation
        image.convert(carla.ColorConverter.CityScapesPalette)
        i = np.array(image.raw_data)
        i = i.reshape((self.im_height, self.im_width, 4))[:, :, :3] # this is to ignore the 4th Alpha channel - up to 3
        self.front_camera = i


    def collision_data(self, event):
        self.collision_hist.append(event)

    def lane_data(self, event):
        self.lane_invade_hist.append(event)
	
    def seed(self, seed=None):
        pass