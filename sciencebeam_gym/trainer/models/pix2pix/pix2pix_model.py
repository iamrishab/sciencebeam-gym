from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import logging

import tensorflow as tf
import argparse

import six
from six.moves.configparser import ConfigParser

from tensorflow.python.lib.io.file_io import FileIO

from sciencebeam_gym.trainer.util import (
  read_examples
)

from sciencebeam_gym.tools.colorize_image import (
  parse_color_map_from_configparser
)

from sciencebeam_gym.trainer.models.pix2pix.pix2pix_core import (
  create_pix2pix_model,
  create_image_summaries,
  create_other_summaries
)


class GraphMode(object):
  TRAIN = 1
  EVALUATE = 2
  PREDICT = 3

def get_logger():
  return logging.getLogger(__name__)


class GraphReferences(object):
  """Holder of base tensors used for training model using common task."""

  def __init__(self):
    self.examples = None
    self.train = None
    self.global_step = None
    self.metric_updates = []
    self.metric_values = []
    self.keys = None
    self.predictions = []
    self.input_jpeg = None
    self.input_uri = None
    self.image_tensor = None
    self.annotation_uri = None
    self.annotation_tensor = None
    self.separate_channel_annotation_tensor = None
    self.class_labels_tensor = None
    self.pred = None
    self.probabilities = None
    self.summary = None
    self.summaries = None
    self.image_tensors = None

def colors_to_dimensions(image_tensor, colors):
  logger = get_logger()
  single_label_tensors = []
  for single_label_color in colors:
    is_color = tf.reduce_all(
      tf.equal(image_tensor, single_label_color),
      axis=-1
    )
    single_label_tensor = tf.where(
      is_color,
      tf.fill(is_color.shape, 1.0),
      tf.fill(is_color.shape, 0.0)
    )
    single_label_tensors.append(single_label_tensor)
  return tf.stack(single_label_tensors, axis=-1)

def batch_dimensions_to_colors_list(image_tensor, colors):
  logger = get_logger()
  batch_images = []
  for i, single_label_color in enumerate(colors):
    batch_images.append(
      tf.expand_dims(
        image_tensor[:, :, :, i],
        axis=-1
      ) * ([x / 255.0 for x in single_label_color])
    )
  return batch_images

def add_summary_image(tensors, name, image):
  tensors.image_tensors[name] = image
  tf.summary.image(name, image)

def convert_image(image_tensor):
  return tf.image.convert_image_dtype(
    image_tensor,
    dtype=tf.uint8,
    saturate=True
  )

def add_simple_summary_image(tensors, name, image_tensor):
  with tf.name_scope(name):
    add_summary_image(
      tensors,
      name,
      convert_image(image_tensor)
    )

def replace_black_with_white_color(image_tensor):
  is_black = tf.reduce_all(
  tf.equal(image_tensor, (0, 0, 0)),
    axis=-1
  )
  is_black = tf.stack([is_black] * 3, axis=-1)
  return tf.where(
    is_black,
    255 * tf.ones_like(image_tensor),
    image_tensor
  )

def combine_image(batch_images, replace_black_with_white=False):
  combined_image = convert_image(
    six.moves.reduce(
      lambda a, b: a + b,
      batch_images
    )
  )
  if replace_black_with_white:
    combined_image = replace_black_with_white_color(combined_image)
  return combined_image

def add_model_summary_images(tensors, dimension_colors, dimension_labels):
  tensors.summaries = {}
  add_simple_summary_image(
    tensors, 'input', tensors.image_tensor
  )
  add_simple_summary_image(
    tensors, 'target', tensors.annotation_tensor
  )
  if dimension_colors:
    batch_images = batch_dimensions_to_colors_list(
      tensors.separate_channel_annotation_tensor,
      dimension_colors
    )
    for i, (batch_image, dimension_label) in enumerate(zip(batch_images, dimension_labels)):
      suffix = "_{}_{}".format(
        i, dimension_label if dimension_label else 'unknown_label'
      )
      add_simple_summary_image(
        tensors, 'targets' + suffix, batch_image
      )
    with tf.name_scope("targets_combined"):
      combined_image = combine_image(batch_images)
      add_summary_image(
        tensors,
        "targets_combined",
        combined_image
      )
    batch_images = batch_dimensions_to_colors_list(
      tensors.pred,
      dimension_colors
    )
    for i, (batch_image, dimension_label) in enumerate(zip(batch_images, dimension_labels)):
      suffix = "_{}_{}".format(
        i, dimension_label if dimension_label else 'unknown_label'
      )
      add_simple_summary_image(
        tensors, 'outputs' + suffix, batch_image
      )
    with tf.name_scope("outputs_combined"):
      combined_image = combine_image(batch_images)
      tensors.summaries['output_image'] = combined_image
      add_summary_image(
        tensors,
        "outputs_combined",
        combined_image
      )
  else:
    add_simple_summary_image(
      tensors,
      "output",
      tensors.pred
    )
    tensors.summaries['output_image'] = tensors.image_tensors['output']

class Model(object):
  def __init__(self, args):
    self.args = args
    self.image_width = 256
    self.image_height = 256
    self.color_map = None
    self.dimension_colors = None
    self.dimension_labels = None
    logger = get_logger()
    if self.args.color_map:
      color_map_config = ConfigParser()
      with FileIO(self.args.color_map, 'r') as config_f:
        color_map_config.readfp(config_f)
      self.color_map = parse_color_map_from_configparser(color_map_config)
      color_label_map = {
        (int(k), int(k), int(k)): v
        for k, v in color_map_config.items('color_labels')
      }
      sorted_keys = sorted(six.iterkeys(self.color_map))
      self.dimension_colors = [self.color_map[k] for k in sorted_keys]
      self.dimension_labels = [color_label_map.get(k) for k in sorted_keys]
      logger.debug("dimension_colors: %s", self.dimension_colors)
      logger.debug("dimension_labels: %s", self.dimension_labels)

  def build_graph(self, data_paths, batch_size, graph_mode):
    logger = get_logger()
    logger.debug('batch_size: %s', batch_size)
    tensors = GraphReferences()
    is_training = (
      graph_mode == GraphMode.TRAIN or
      graph_mode == GraphMode.EVALUATE
    )
    if data_paths:
      tensors.keys, tensors.examples = read_examples(
        data_paths,
        shuffle=(graph_mode == GraphMode.TRAIN),
        num_epochs=None if is_training else 2
      )
    else:
      tensors.examples = tf.placeholder(tf.string, name='input', shape=(None,))
    with tf.name_scope('inputs'):
      feature_map = {
        'input_uri':
          tf.FixedLenFeature(
            shape=[], dtype=tf.string, default_value=['']
          ),
        'annotation_uri':
          tf.FixedLenFeature(
            shape=[], dtype=tf.string, default_value=['']
          ),
        'input_image':
          tf.FixedLenFeature(
            shape=[], dtype=tf.string
          ),
        'annotation_image':
          tf.FixedLenFeature(
            shape=[], dtype=tf.string
          )
      }
      logging.info('tensors.examples: %s', tensors.examples)
    parsed = tf.parse_single_example(tensors.examples, features=feature_map)

    tensors.image_tensors = {}

    tensors.input_uri = tf.squeeze(parsed['input_uri'])
    tensors.annotation_uri = tf.squeeze(parsed['annotation_uri'])
    raw_input_image = tf.squeeze(parsed['input_image'])
    logging.info('raw_input_image: %s', raw_input_image)
    raw_annotation_image = tf.squeeze(parsed['annotation_image'])
    tensors.image_tensor = tf.image.decode_png(raw_input_image, channels=3)
    tensors.annotation_tensor = tf.image.decode_png(raw_annotation_image, channels=3)

    # TODO resize_images and tf.cast did not work on input image
    #   but did work on annotation image
    tensors.image_tensor = tf.image.resize_image_with_crop_or_pad(
      tensors.image_tensor, self.image_height, self.image_width
    )

    tensors.image_tensor = tf.image.convert_image_dtype(tensors.image_tensor, tf.float32)

    tensors.annotation_tensor = tf.image.resize_image_with_crop_or_pad(
      tensors.annotation_tensor, self.image_height, self.image_width
    )

    if self.dimension_colors:
      tensors.separate_channel_annotation_tensor = colors_to_dimensions(
        tensors.annotation_tensor,
        self.dimension_colors
      )
    else:
      tensors.annotation_tensor = tf.image.convert_image_dtype(tensors.annotation_tensor, tf.float32)
      tensors.separate_channel_annotation_tensor = tensors.annotation_tensor

    (
      tensors.input_uri,
      tensors.annotation_uri,
      tensors.image_tensor,
      tensors.annotation_tensor,
      tensors.separate_channel_annotation_tensor
    ) = tf.train.batch(
      [
        tensors.input_uri,
        tensors.annotation_uri,
        tensors.image_tensor,
        tensors.annotation_tensor,
        tensors.separate_channel_annotation_tensor
      ],
      batch_size=batch_size
    )

    pix2pix_model = create_pix2pix_model(
      tensors.image_tensor,
      tensors.separate_channel_annotation_tensor,
      self.args
    )

    tensors.global_step = pix2pix_model.global_step
    tensors.train = pix2pix_model.train
    tensors.class_labels_tensor = tensors.annotation_tensor
    tensors.pred = pix2pix_model.outputs
    tensors.probabilities = pix2pix_model.outputs
    tensors.metric_values = [pix2pix_model.discrim_loss]

    add_model_summary_images(tensors, self.dimension_colors, self.dimension_labels)

    # tensors.summaries = create_summaries(pix2pix_model)
    create_other_summaries(pix2pix_model)

    tensors.summary = tf.summary.merge_all()
    return tensors

  def build_train_graph(self, data_paths, batch_size):
    return self.build_graph(data_paths, batch_size, GraphMode.TRAIN)

  def build_eval_graph(self, data_paths, batch_size):
    return self.build_graph(data_paths, batch_size, GraphMode.EVALUATE)

  def initialize(self, session):
    pass

  def format_metric_values(self, metric_values):
    """Formats metric values - used for logging purpose."""

    # Early in training, metric_values may actually be None.
    loss_str = 'N/A'
    accuracy_str = 'N/A'
    try:
      loss_str = '%.3f' % metric_values[0]
      accuracy_str = '%.3f' % metric_values[1]
    except (TypeError, IndexError):
      pass

    return '%s, %s' % (loss_str, accuracy_str)

def model_args_parser():
  parser = argparse.ArgumentParser()
  parser.add_argument("--ngf", type=int, default=64, help="number of generator filters in first conv layer")
  parser.add_argument("--ndf", type=int, default=64, help="number of discriminator filters in first conv layer")
  parser.add_argument("--lr", type=float, default=0.0002, help="initial learning rate for adam")
  parser.add_argument("--beta1", type=float, default=0.5, help="momentum term of adam")
  parser.add_argument("--l1_weight", type=float, default=100.0, help="weight on L1 term for generator gradient")
  parser.add_argument("--gan_weight", type=float, default=1.0, help="weight on GAN term for generator gradient")

  parser.add_argument(
    '--color_map',
    type=str,
    help='The path to the color map configuration.'
  )
  return parser


def create_model(argv=None):
  """Factory method that creates model to be used by generic task.py."""
  parser = model_args_parser()
  args, task_args = parser.parse_known_args(argv)
  return Model(args), task_args