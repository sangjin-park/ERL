import os
import time
import random
import numpy as np
from tqdm import tqdm
import tensorflow as tf
import gym.wrappers
import time
from scipy.misc import imsave
from .base import BaseModel
from .history import History
from .experience_replay import DataSet
# from .replay_memory import ReplayMemory
from .ops import linear, conv2d, clipped_error
from .utils import get_time, save_pkl, load_pkl, my_imshow
from scipy.misc import imresize
from .GatedPixelCNN.func import process_density_images, process_density_input, get_network
from math import log, exp, pow
from .utils import rgb2gray, imresize
from gym.envs.atari.atari_env import ACTION_MEANING
import pyglet
from functools import reduce

class Agent(BaseModel):
  def __init__(self, config, environment, sess):
    super(Agent, self).__init__(config)
    self.sess = sess
    self.weight_dir = 'weights'
    self.density_model = get_network("density")
    self.last_play = 0
    self.env = environment
    self.history = History(self.config)
    # self.memory = ReplayMemory(self.config, self.model_dir)
    self.memory = DataSet(self.config, np.random.RandomState())
    self.ep_transition_cache = []
    self.ep_steps = 0
    with tf.variable_scope('step'):
      self.step_op = tf.Variable(0, trainable=False, name='step')
      self.step_input = tf.placeholder('int32', None, name='step_input')
      self.step_assign_op = self.step_op.assign(self.step_input)

    self.build_dqn()

  def neural_psc(self, frame, step):
    last_frame = process_density_images(frame)
    density_input = process_density_input(last_frame)

    prob = self.density_model.prob_evaluate(self.sess, density_input, True) + 1e-8
    prob_dot = self.density_model.prob_evaluate(self.sess, density_input) + 1e-8
    prob_ratio = prob_dot / prob
    pred_gain = np.sum(np.log(prob_ratio))

    # TODO: DETERMINE WHETHER TO SUBSTRACT THE PSC_START
    psc_reward = pow((exp(0.1*pow(step + 1, -0.5) * max(0, pred_gain)) - 1), 0.5)
    return psc_reward

  def train(self):
    start_step = self.step_op.eval()
    start_time = time.time()

    num_game, self.update_count, ep_reward = 0, 0, 0.
    total_reward, self.total_loss, self.total_q = 0., 0., 0.
    max_avg_ep_reward = 0
    ep_rewards, actions = [], []

    screen, reward, action, terminal = self.env.new_random_game()

    for _ in range(self.history_length):
      self.history.add(screen)

    for self.step in tqdm(list(range(start_step, self.max_step)), ncols=70, initial=start_step):
      if self.step == self.learn_start:
        num_game, self.update_count, ep_reward = 0, 0, 0.
        total_reward, self.total_loss, self.total_q = 0., 0., 0.
        ep_rewards, actions = [], []

      # 1. predict
      action = self.predict(self.history.get())
      # 2. act
      screen, reward, terminal = self.env.act(action, is_training=True)
      # 3. observe
      if self.ep_steps > self.max_ep_steps:
        terminal = True

      self.observe(screen, reward, action, terminal)
      self.ep_steps += 1

      if terminal:
        self.ep_steps = 0
        # process the cut from the ep_transition_cache
        # TODO resize the ep_transition to the down_sample_image
        # TODO re-evaluate the image when train.
        ep_length = len(self.ep_transition_cache)

        if ep_length > self.bonus_preserve:
          # TODO: seperate the autoencoder's batchsize from the DQN batchsize
          ep_bonus_list = []
          for idx in range(0, ep_length):
            screen = self.ep_transition_cache[idx][0]
            ep_bonus_list.append(self.neural_psc(imresize(screen, (42, 42)) / 255., self.step-ep_length+idx))

          # ONLY PRESERVE THE first several ones.
          ep_bonus = np.array(ep_bonus_list)
          ep_bonus_rank = ep_bonus.argsort()
          zero_idx = ep_bonus_rank[:-self.bonus_preserve]
          ep_bonus[zero_idx] = 0

          for idx in range(0, ep_length):
            tuple = self.ep_transition_cache[idx]
            self.memory.add_sample(tuple[0], tuple[1], min(tuple[2] + ep_bonus[idx], 1), tuple[3])

        screen, reward, action, terminal = self.env.new_random_game()

        for _ in range(self.history_length):
          self.history.add(screen)
        self.ep_transition_cache = []

        num_game += 1
        ep_rewards.append(ep_reward)
        ep_reward = 0.
      else:
        ep_reward += reward

      actions.append(action)
      total_reward += reward

      if self.step >= self.learn_start:
        if self.step % self.test_step == self.test_step - 1:
          avg_reward = total_reward / self.test_step
          avg_loss = self.total_loss / self.update_count
          avg_q = self.total_q / self.update_count

          try:
            max_ep_reward = np.max(ep_rewards)
            min_ep_reward = np.min(ep_rewards)
            avg_ep_reward = np.mean(ep_rewards)
          except:
            max_ep_reward, min_ep_reward, avg_ep_reward = 0, 0, 0

          print('\navg_r: %.4f, avg_l: %.6f, avg_q: %3.6f, avg_ep_r: %.4f, max_ep_r: %.4f, min_ep_r: %.4f, # game: %d, psc_reward: %d' \
              % (avg_reward, avg_loss, avg_q, avg_ep_reward, max_ep_reward, min_ep_reward, num_game, self.psc_reward))

          if max_avg_ep_reward * 0.9 <= avg_ep_reward:
            self.step_assign_op.eval({self.step_input: self.step + 1})
            self.save_model(self.step + 1)

            max_avg_ep_reward = max(max_avg_ep_reward, avg_ep_reward)

          if self.step > 180:
            self.inject_summary({
                'average.reward': avg_reward,
                'average.loss': avg_loss,
                'average.q': avg_q,
                'episode.max reward': max_ep_reward,
                'episode.min reward': min_ep_reward,
                'episode.avg reward': avg_ep_reward,
                'episode.num of game': num_game,
                'episode.rewards': ep_rewards,
                'episode.actions': actions,
                'training.learning_rate': self.learning_rate_op.eval({self.learning_rate_step: self.step}),
              }, self.step)

          num_game = 0
          total_reward = 0.
          self.total_loss = 0.
          self.total_q = 0.
          self.update_count = 0
          ep_reward = 0.
          ep_rewards = []
          actions = []

  def predict(self, s_t, test=False):
    if test:
      ep = 0.01
    else:
      ep = self.ep_end + max(0., (self.ep_start - self.ep_end)
          * (self.ep_end_t - max(0., self.step - self.learn_start)) / self.ep_end_t)

    if random.random() < ep:
      action = random.randrange(self.env.action_size)
    else:
      action = self.q_action.eval({self.s_t: [s_t]})[0]

    return action

  def predict2(self, s_t, test=False):
    if test:
      ep = 0.01
    else:
      ep = self.ep_end + max(0., (self.ep_start - self.ep_end)
          * (self.ep_end_t - max(0., self.step - self.learn_start)) / self.ep_end_t)

    sess = tf.get_default_session()
    action0, l3_ = sess.run([self.q_action, self.l3], feed_dict={self.s_t: [s_t]})
    l3 = l3_[0]

    if random.random() < ep:
      action = random.randrange(self.env.action_size)
    else:
      action = action0[0]

    return action, l3

  def observe(self, screen, reward, action, terminal):
    reward = max(self.min_reward, min(self.max_reward, reward))
    self.history.add(screen)

    # delay it until the terminal state.
    # In this setting, if we haven't trained autoencoder.
    # No selective sample happened.
    # By default, the initial 100000 states could all be rare states.

    # modify the reward here, step is the t. No relationship with train_frequency.
    # self.ae.memory.add(imresize(screen, self.ae_screen_shape))
    # TODO: ADD 42 42 TO THE CONFIG FILE
    # need to use this step to update the pixelCNN network.


    # print self.psc_reward, self.step
    # if self.step> 10000:
    #   imsave("fuck/step%i_%f.png" % (self.step, self.psc_reward), screen)

    if self.step > self.psc_start:
      self.ep_transition_cache.append((screen, action, reward, terminal))
    else:
      self.psc_reward = self.neural_psc(imresize(screen, (42, 42)) / 255., self.step)
      self.memory.add_sample(screen, action, reward, terminal)

    # if self.step > self.psc_start:
      # reward += self.psc_reward
    # reward = np.clip(reward, -1, 1)
    # self.memory.add(screen, reward, action, terminal) # its initial state
    # self.memory.add_sample(screen, action, reward, terminal)

    if self.step > self.learn_start:
      if self.step % self.train_frequency == 0:
        self.q_learning_mini_batch()

      if self.step % self.target_q_update_step == self.target_q_update_step - 1:
        self.update_target_q_network()

  def q_learning_mini_batch(self):
    if self.memory.size < self.history_length:
      return
    else:
      s, action, reward, terminal, R = self.memory.random_batch(self.batch_size)
    
    #print np.max(R)
    s_t = s[..., :self.history_length]
    s_t_plus_1 = s[..., -self.history_length:]
    t = time.time()
    if self.double_q:
      # Double Q-learning
      pred_action = self.q_action.eval({self.s_t: s_t_plus_1})

      q_t_plus_1_with_pred_action = self.target_q_with_idx.eval({
        self.target_s_t: s_t_plus_1,
        self.target_q_idx: [[idx, pred_a] for idx, pred_a in enumerate(pred_action)]
      })
      target_q_t = (1. - terminal) * self.discount * q_t_plus_1_with_pred_action + reward

    else:
      q_t_plus_1 = self.target_q.eval({self.target_s_t: s_t_plus_1})

      terminal = np.array(terminal) + 0.
      max_q_t_plus_1 = np.max(q_t_plus_1, axis=1)
      target_q_t = (1. - terminal) * self.discount * max_q_t_plus_1 + reward
    # tmp = target_q_t
    target_q_t = (1 - self.beta) * target_q_t + (1. - terminal) * self.beta * R
    # print tmp, target_q_t
    _, q_t, loss, summary_str = self.sess.run([self.optim, self.q, self.loss, self.q_summary], {
      self.target_q_t: target_q_t,
      self.action: action,
      self.s_t: s_t,
      self.learning_rate_step: self.step,
    })


    self.writer.add_summary(summary_str, self.step)
    self.total_loss += loss
    self.total_q += q_t.mean()
    self.update_count += 1


  def build_dqn(self):
    self.w = {}
    self.t_w = {}

    #initializer = tf.contrib.layers.xavier_initializer()
    initializer = tf.truncated_normal_initializer(0, 0.02)
    activation_fn = tf.nn.relu

    # training network
    with tf.variable_scope('prediction'):
      if self.cnn_format == 'NHWC':
        self.s_t = tf.placeholder('float32',
            [None, self.screen_height, self.screen_width, self.history_length], name='s_t')
      else:
        self.s_t = tf.placeholder('float32',
            [None, self.history_length, self.screen_height, self.screen_width], name='s_t')

      self.l1, self.w['l1_w'], self.w['l1_b'] = conv2d(self.s_t,
          32, [8, 8], [4, 4], initializer, activation_fn, self.cnn_format, name='l1')
      self.l2, self.w['l2_w'], self.w['l2_b'] = conv2d(self.l1,
          64, [4, 4], [2, 2], initializer, activation_fn, self.cnn_format, name='l2')
      self.l3, self.w['l3_w'], self.w['l3_b'] = conv2d(self.l2,
          64, [3, 3], [1, 1], initializer, activation_fn, self.cnn_format, name='l3')

      shape = self.l3.get_shape().as_list()
      self.l3_flat = tf.reshape(self.l3, [-1, reduce(lambda x, y: x * y, shape[1:])])

      if self.dueling:
        self.value_hid, self.w['l4_val_w'], self.w['l4_val_b'] = \
            linear(self.l3_flat, 512, activation_fn=activation_fn, name='value_hid')

        self.adv_hid, self.w['l4_adv_w'], self.w['l4_adv_b'] = \
            linear(self.l3_flat, 512, activation_fn=activation_fn, name='adv_hid')

        self.value, self.w['val_w_out'], self.w['val_w_b'] = \
          linear(self.value_hid, 1, name='value_out')

        self.advantage, self.w['adv_w_out'], self.w['adv_w_b'] = \
          linear(self.adv_hid, self.env.action_size, name='adv_out')

        # Average Dueling
        self.q = self.value + (self.advantage - 
          tf.reduce_mean(self.advantage, reduction_indices=1, keep_dims=True))
      else:
        self.l4, self.w['l4_w'], self.w['l4_b'] = linear(self.l3_flat, 512, activation_fn=activation_fn, name='l4')
        self.q, self.w['q_w'], self.w['q_b'] = linear(self.l4, self.env.action_size, name='q')

      self.q_action = tf.argmax(self.q, dimension=1)

      q_summary = []
      avg_q = tf.reduce_mean(self.q, 0)
      for idx in range(self.env.action_size):
        q_summary.append(tf.summary.histogram('q/%s' % idx, avg_q[idx]))
      self.q_summary = tf.summary.merge(q_summary, 'q_summary')

    # target network
    with tf.variable_scope('target'):
      if self.cnn_format == 'NHWC':
        self.target_s_t = tf.placeholder('float32', 
            [None, self.screen_height, self.screen_width, self.history_length], name='target_s_t')
      else:
        self.target_s_t = tf.placeholder('float32', 
            [None, self.history_length, self.screen_height, self.screen_width], name='target_s_t')

      self.target_l1, self.t_w['l1_w'], self.t_w['l1_b'] = conv2d(self.target_s_t, 
          32, [8, 8], [4, 4], initializer, activation_fn, self.cnn_format, name='target_l1')
      self.target_l2, self.t_w['l2_w'], self.t_w['l2_b'] = conv2d(self.target_l1,
          64, [4, 4], [2, 2], initializer, activation_fn, self.cnn_format, name='target_l2')
      self.target_l3, self.t_w['l3_w'], self.t_w['l3_b'] = conv2d(self.target_l2,
          64, [3, 3], [1, 1], initializer, activation_fn, self.cnn_format, name='target_l3')

      shape = self.target_l3.get_shape().as_list()
      self.target_l3_flat = tf.reshape(self.target_l3, [-1, reduce(lambda x, y: x * y, shape[1:])])

      if self.dueling:
        self.t_value_hid, self.t_w['l4_val_w'], self.t_w['l4_val_b'] = \
            linear(self.target_l3_flat, 512, activation_fn=activation_fn, name='target_value_hid')

        self.t_adv_hid, self.t_w['l4_adv_w'], self.t_w['l4_adv_b'] = \
            linear(self.target_l3_flat, 512, activation_fn=activation_fn, name='target_adv_hid')

        self.t_value, self.t_w['val_w_out'], self.t_w['val_w_b'] = \
          linear(self.t_value_hid, 1, name='target_value_out')

        self.t_advantage, self.t_w['adv_w_out'], self.t_w['adv_w_b'] = \
          linear(self.t_adv_hid, self.env.action_size, name='target_adv_out')

        # Average Dueling
        self.target_q = self.t_value + (self.t_advantage - 
          tf.reduce_mean(self.t_advantage, reduction_indices=1, keep_dims=True))
      else:
        self.target_l4, self.t_w['l4_w'], self.t_w['l4_b'] = \
            linear(self.target_l3_flat, 512, activation_fn=activation_fn, name='target_l4')
        self.target_q, self.t_w['q_w'], self.t_w['q_b'] = \
            linear(self.target_l4, self.env.action_size, name='target_q')

      self.target_q_idx = tf.placeholder('int32', [None, None], 'outputs_idx')
      self.target_q_with_idx = tf.gather_nd(self.target_q, self.target_q_idx)

    with tf.variable_scope('pred_to_target'):
      self.t_w_input = {}
      self.t_w_assign_op = {}

      for name in list(self.w.keys()):
        self.t_w_input[name] = tf.placeholder('float32', self.t_w[name].get_shape().as_list(), name=name)
        self.t_w_assign_op[name] = self.t_w[name].assign(self.t_w_input[name])

    # optimizer
    with tf.variable_scope('optimizer'):
      self.target_q_t = tf.placeholder('float32', [None], name='target_q_t')
      self.action = tf.placeholder('int64', [None], name='action')

      action_one_hot = tf.one_hot(self.action, self.env.action_size, 1.0, 0.0, name='action_one_hot')
      q_acted = tf.reduce_sum(self.q * action_one_hot, reduction_indices=1, name='q_acted')

      self.delta = self.target_q_t - q_acted

      self.global_step = tf.Variable(0, trainable=False)

      self.loss = tf.reduce_mean(clipped_error(self.delta), name='loss')
      self.learning_rate_step = tf.placeholder('int64', None, name='learning_rate_step')
      self.learning_rate_op = tf.maximum(self.learning_rate_minimum,
          tf.train.exponential_decay(
              self.learning_rate,
              self.learning_rate_step,
              self.learning_rate_decay_step,
              self.learning_rate_decay,
              staircase=True))
      self.optim = tf.train.RMSPropOptimizer(
          self.learning_rate_op, momentum=0.95, epsilon=0.01).minimize(self.loss)

    with tf.variable_scope('summary'):
      scalar_summary_tags = ['average.reward', 'average.loss', 'average.q', \
          'episode.max reward', 'episode.min reward', 'episode.avg reward', 'episode.num of game', 'training.learning_rate']

      self.summary_placeholders = {}
      self.summary_ops = {}

      for tag in scalar_summary_tags:
        self.summary_placeholders[tag] = tf.placeholder('float32', None, name=tag.replace(' ', '_'))
        self.summary_ops[tag]  = tf.summary.scalar("%s-%s/%s" % (self.env_name, self.env_type, tag), self.summary_placeholders[tag])

      histogram_summary_tags = ['episode.rewards', 'episode.actions']

      for tag in histogram_summary_tags:
        self.summary_placeholders[tag] = tf.placeholder('float32', None, name=tag.replace(' ', '_'))
        self.summary_ops[tag]  = tf.summary.histogram(tag, self.summary_placeholders[tag])

      self.writer = tf.summary.FileWriter('./logs/%s' % self.model_dir, self.sess.graph)

    tf.initialize_all_variables().run()

    self._saver = tf.train.Saver(list(self.w.values()) + [self.step_op], max_to_keep=30)

    self.load_model()
    self.update_target_q_network()

  def update_target_q_network(self):
    for name in list(self.w.keys()):
      self.t_w_assign_op[name].eval({self.t_w_input[name]: self.w[name].eval()})

  def save_weight_to_pkl(self):
    if not os.path.exists(self.weight_dir):
      os.makedirs(self.weight_dir)

    for name in list(self.w.keys()):
      save_pkl(self.w[name].eval(), os.path.join(self.weight_dir, "%s.pkl" % name))

  def load_weight_from_pkl(self, cpu_mode=False):
    with tf.variable_scope('load_pred_from_pkl'):
      self.w_input = {}
      self.w_assign_op = {}

      for name in list(self.w.keys()):
        self.w_input[name] = tf.placeholder('float32', self.w[name].get_shape().as_list(), name=name)
        self.w_assign_op[name] = self.w[name].assign(self.w_input[name])

    for name in list(self.w.keys()):
      self.w_assign_op[name].eval({self.w_input[name]: load_pkl(os.path.join(self.weight_dir, "%s.pkl" % name))})

    self.update_target_q_network()

  def inject_summary(self, tag_dict, step):
    summary_str_lists = self.sess.run([self.summary_ops[tag] for tag in list(tag_dict.keys())], {
      self.summary_placeholders[tag]: value for tag, value in list(tag_dict.items())
    })
    for summary_str in summary_str_lists:
      self.writer.add_summary(summary_str, self.step)

  def play(self, n_step=10000, n_episode=100, test_ep=True, render=False):
    if test_ep == None:
      test_ep = self.ep_end

    test_history = History(self.config)

    if not self.display:
      gym_dir = './tmp/%s-%s' % (self.env_name, get_time())
      # self.env.env.monitor.start(gym_dir)
      monitor = gym.wrappers.Monitor(self.env.env, gym_dir)
    else:
      monitor = self.env.env
      # cv2.startWindowThread()
      # cv2.namedWindow("view")

    best_reward, best_idx = 0, 0
    ep_rewards = []
    for idx in tqdm(range(n_episode),ncols=70):
      screen = monitor.reset()
      screen = imresize(rgb2gray(screen), (110, 84))
      screen = screen[18:102, :]
      current_reward = 0

      # if not os.path.exists("fuck/epoch%i" % idx):
        # os.mkdir("fuck/epoch%i" % idx)
      for _ in range(self.history_length):
        test_history.add(screen)

      for t in range(n_step):
        # 1. predict
        action, l3 = self.predict2(test_history.get(), test_ep)
        # 2. act
        screen, reward, terminal, _ = monitor.step(action)
        print(t, action, ACTION_MEANING[action], reward)

        org_screen = screen
        screen = imresize(rgb2gray(screen), (110, 84))
        if self.display:
          monitor.render()

          # img = np.broadcast_to(arr.T, (3, arr.shape[1], arr.shape[0])).T
          # my_imshow('org_screen', org_screen, 3)
          v_l3 = np.array([l3.T[0:21].mean(axis=0) , l3.T[21:42].mean(axis=0), l3.T[42:64].mean(axis=0)]).T
          my_imshow('l3', v_l3, 70)

          # plt.imshow(screen)
          # plt.show()
          # cv2.imshow("view", screen)

        screen = screen[18:102, :]
        # 3. observe
        test_history.add(screen)

        current_reward += reward
        if terminal:

          break

      print("GET REWARD", current_reward)
      ep_rewards.append(current_reward)
      if current_reward > best_reward:
        best_reward = current_reward
        best_idx = idx


    print("="*30)
    print(" [%d] Best reward : %d" % (best_idx, best_reward), end=' ')
    print("Average reward: %f" %  np.mean(ep_rewards))
    print("="*30)

    if not self.display:
      monitor.close()
      #gym.upload(gym_dir, writeup='https://github.com/devsisters/DQN-tensorflow', api_key='')
