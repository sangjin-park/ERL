from gym.envs.registration import register
import gym.envs.atari.atari_env
from gym import error, spaces

game = 'montezuma_revenge'
g = game
name = ''.join([g.capitalize() for g in game.split('_')])
obs_type = 'image'
nondeterministic = False

register(
    id='{}-v6'.format(name),
    #entry_point='gym.envs.atari:AtariEnv',
    entry_point='montezuma_simple:ReducedAtariEnv',
    kwargs={'game': game, 'obs_type': obs_type, 'repeat_action_probability': 0.25},
    max_episode_steps=10000,
    nondeterministic=nondeterministic,
)

class ReducedAtariEnv(gym.envs.atari.atari_env.AtariEnv):
    def __init__(self, game='pong', obs_type='ram', frameskip=(2, 5), repeat_action_probability=0.):
        gym.envs.atari.atari_env.AtariEnv.__init__(self, game, obs_type, frameskip, repeat_action_probability)

        self._action_set = [0, 1, 2, 3, 4, 5, 11, 12]
        self.action_space = spaces.Discrete(len(self._action_set))
