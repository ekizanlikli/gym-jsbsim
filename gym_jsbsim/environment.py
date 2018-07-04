import gym
import subprocess
import time
import numpy as np
from .tasks import TaskModule, SteadyLevelFlightTask
from .simulation import Simulation
from typing import Type


class JsbSimEnv(gym.Env):
    """
    A class wrapping the JSBSim flight dynamics module (FDM) for simulating
    aircraft as an RL environment conforming to the OpenAI Gym Env
    interface.

    An JsbSimEnv is instantiated with a TaskModule that implements a specific
    aircraft control task through additional task-related observation/action
    variables and reward calculation.

    The following API methods will be implemented between JsbSimEnv:
        step
        reset
        render
        close
        seed

    Along with the following attributes:
        action_space: The Space object corresponding to valid actions
        observation_space: The Space object corresponding to valid observations
        reward_range: A tuple corresponding to the min and max possible rewards

    ATTRIBUTION: this class is based on the OpenAI Gym Env API. Method
    docstrings have been taken from the OpenAI API and modified where required.
    """
    DT_HZ: int = 120  # JSBSim integration frequency [Hz]
    FLIGHTGEAR_TIME_FACTOR = 1
    metadata = {'render.modes': ['human', 'flightgear']}

    def __init__(self, task_type: Type[TaskModule], aircraft_name: str='c172p',
                 agent_interaction_freq: int=10):
        """
        Constructor. Inits some internal state, but JsbSimEnv.reset() must be
        called first before interacting with environment.

        :param task_type: a TaskModule class of the task agent is to perform
        :param agent_interaction_freq: int, how many times per second the agent
            should interact with environment.
        :param shaped_reward: agent reward undergoes potential-based shaping if True
        """
        if agent_interaction_freq > self.DT_HZ:
            raise ValueError('agent interaction frequency must be less than '
                             'or equal to JSBSim integration frequency of '
                             f'{self.DT_HZ} Hz.')
        self.sim: Simulation = None
        self.sim_steps: int = self.DT_HZ // agent_interaction_freq
        self.aircraft = aircraft_name
        self.task = task_type()
        # set Space objects
        self.observation_space: gym.spaces.Box = self.task.get_observation_space()
        self.action_space: gym.spaces.Box = self.task.get_action_space()
        self.flightgear_process: subprocess.Popen = None
        self.step_delay = None

    def step(self, action: np.ndarray):
        """
        Run one timestep of the environment's dynamics. When end of
        episode is reached, you are responsible for calling `reset()`
        to reset this environment's state.
        Accepts an action and returns a tuple (observation, reward, done, info).

        Args:
            action: collection of floats, the agent's action. Must have same length
                as number of action variables.
        Returns:
            observation (object): agent's observation of the current environment
            reward (float) : amount of reward returned after previous action
            done (boolean): whether the episode has ended, in which case further step() calls are undefined
            info (dict): contains auxiliary diagnostic information (helpful for debugging, and sometimes learning)
        """
        if not (action.shape == self.action_space.shape):
            raise ValueError('mismatch between action and action space size')

        return self.task.task_step(self.sim, action, self.sim_steps)

    def reset(self):
        """
        Resets the state of the environment and returns an initial observation.

        :return: array, the initial observation of the space.
        """
        if self.sim:
            self.sim.close()
        init_conditions = self.task.get_initial_conditions()
        self.sim = Simulation(sim_dt=(1.0 / self.DT_HZ),
                              aircraft_model_name=self.aircraft,
                              init_conditions=init_conditions)
        state = self.task.observe_first_state(self.sim)

        if self.flightgear_process:
            self.sim.enable_flightgear_output()
            self.sim.set_simulation_time_factor(self.FLIGHTGEAR_TIME_FACTOR)

        return np.array(state)

    def render(self, mode='human', action_names=None, action_values=None):
        """Renders the environment.
        The set of supported modes varies per environment. (And some
        environments do not support rendering at all.) By convention,
        if mode is:
        - human: render to the current display or terminal and
          return nothing. Usually for human consumption.
        - rgb_array: Return an numpy.ndarray with shape (x, y, 3),
          representing RGB values for an x-by-y pixel image, suitable
          for turning into a video.
        - ansi: Return a string (str) or StringIO.StringIO containing a
          terminal-style text representation. The text can include newlines
          and ANSI escape sequences (e.g. for colors).
        Note:
            Make sure that your class's metadata 'render.modes' key includes
              the list of supported modes. It's recommended to call super()
              in implementations to use the functionality of this method.

        :param mode: str, the mode to render with
        :param action_names: list of str, the JSBSim properties modified
            by agent action
        :param action_values: list of numbers, the value of the action at
            the same index in action_names
        Example:
        class MyEnv(Env):
            metadata = {'render.modes': ['human', 'rgb_array']}
            def render(self, mode='human'):
                if mode == 'rgb_array':
                    return np.array(...) # return RGB frame suitable for video
                elif mode is 'human':
                    ... # pop up a window and render
                else:
                    super(MyEnv, self).render(mode=mode) # just raise an exception
        """
        if mode == 'human':
            self.sim.plot(action_names=action_names, action_values=action_values)
        elif mode == 'flightgear':
            if not self.flightgear_process:
                self.sim.enable_flightgear_output()
                self.sim.set_simulation_time_factor(self.FLIGHTGEAR_TIME_FACTOR)
                self._launch_flightgear()
                # loop until we see FlightGear is ready to render
                ready_message = 'loading cities done'
                while True:
                    msg_out = self.flightgear_process.stdout.readline().decode()
                    if ready_message in msg_out:
                        gym.logger.info('FlightGear loading complete; entering world')
                        break
                    else:
                        time.sleep(0.001)

        else:
            super().render(mode=mode)

    def _launch_flightgear(self):
        TYPE = 'socket'
        DIRECTION = 'in'
        RATE = 60
        SERVER = ''
        PORT = 5550
        PROTOCOL = 'udp'

        flightgear_cmd = 'fgfs'
        aircraft_arg = '--aircraft=' + self.sim.get_model_name()
        flight_model_arg = '--native-fdm=' + f'{TYPE},{DIRECTION},{RATE},{SERVER},{PORT},{PROTOCOL}'
        flight_model_type_arg = '--fdm=' + 'external'

        cmd_line_args = (flightgear_cmd, aircraft_arg, flight_model_arg, flight_model_type_arg)
        gym.logger.info(f'Subprocess: "{cmd_line_args}"')
        self.flightgear_process = subprocess.Popen(
            cmd_line_args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        gym.logger.info('Started FlightGear')

    def close(self):
        """Override _close in your subclass to perform any necessary cleanup.
        Environments will automatically close() themselves when
        garbage collected or when the program exits.
        """
        if self.sim:
            self.sim.close()
        if self.flightgear_process:
            self.flightgear_process.kill()

    def seed(self, seed=None):
        """Sets the seed for this env's random number generator(s).
        Note:
            Some environments use multiple pseudorandom number generators.
            We want to capture all such seeds used in order to ensure that
            there aren't accidental correlations between multiple generators.
        Returns:
            list<bigint>: Returns the list of seeds used in this env's random
              number generators. The first value in the list should be the
              "main" seed, or the value which a reproducer should pass to
              'seed'. Often, the main seed equals the provided 'seed', but
              this won't be true if seed=None, for example.
        """
        gym.logger.warn("Could not seed environment %s", self)
        return


# convenience classes for specific task/aircraft combos
class SteadyLevelFlightCessnaEnv(JsbSimEnv):
    def __init__(self):
        super().__init__(task_type=SteadyLevelFlightTask, aircraft_name='c172p')