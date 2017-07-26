import time
import cPickle
import numpy as np
import pyglet
import tensorflow as tf

try:
  from scipy.misc import imresize
except:
  import cv2
  imresize = cv2.resize

def rgb2gray(image):
  return np.dot(image[...,:3], [0.299, 0.587, 0.114])

def timeit(f):
  def timed(*args, **kwargs):
    start_time = time.time()
    result = f(*args, **kwargs)
    end_time = time.time()

    print("   [-] %s : %2.5f sec" % (f.__name__, end_time - start_time))
    return result
  return timed

def get_time():
  return time.strftime("%Y-%m-%d_%H:%M:%S", time.gmtime())

@timeit
def save_pkl(obj, path):
  with open(path, 'w') as f:
    cPickle.dump(obj, f)
    print("  [*] save %s" % path)

@timeit
def load_pkl(path):
  with open(path) as f:
    obj = cPickle.load(f)
    print("  [*] load %s" % path)
    return obj

@timeit
def save_npy(obj, path):
  np.save(path, obj)
  print("  [*] save %s" % path)

@timeit
def load_npy(path):
  obj = np.load(path)
  print("  [*] load %s" % path)
  return obj

_windows = {}
def my_imshow(window, img, zoom=1):

  img_width, img_height = img.shape[1], img.shape[0]
  width = img_width * zoom
  height = img_height * zoom
  if zoom != 1:
    img = imresize(img, (height, width), 'nearest')

  image = pyglet.image.ImageData(width, height, 'RGB', img.tobytes(), pitch=width * -3)

  if type(window) != pyglet.window.Window:
    if not _windows.has_key(window):
      _window = pyglet.window.Window(width=width, height=height)
      _windows[window] = _window
    window = _windows[window]

  window.clear()
  if (width, height) != window.get_size():
    window.set_size(width, height)
  window.switch_to()
  window.dispatch_events()
  image.blit(0, 0)
  window.flip()