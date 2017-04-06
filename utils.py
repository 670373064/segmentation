import json
import numpy as np
import tensorflow as tf
import matplotlib.pyplot as plt
# % matplotlib inline

with open('config.json', 'r') as f:
    conf = json.load(f)

def weight_variable(shape, stddev=0.1):
    initial = tf.truncated_normal(shape, stddev=stddev)
    return tf.Variable(initial)

def bias_variable(shape):
    initial = tf.constant(0.1, shape=shape)
    return tf.Variable(initial)

def variable_summaries(var):
    """Attach a lot of summaries to a Tensor, for TensorBoard visualization. """

    with tf.name_scope('summaries'):
        mean = tf.reduce_mean(var)
        tf.summary.scalar('mean', mean)
        with tf.name_scope('stddev'):
            stddev = tf.sqrt(tf.reduce_mean(tf.square(var - mean)))
        tf.summary.scalar('stddev', stddev)
        tf.summary.scalar('max', tf.reduce_max(var))
        tf.summary.scalar('min', tf.reduce_min(var))
        tf.summary.histogram('histogram', var)

def max_pool_optional_norm(x, n, to_norm=eval(conf['USE_BATCH_NORM'])):
    pool = tf.nn.max_pool3d(x, ksize=[1, n, n, n, 1], strides=[1, n, n, n, 1], padding='VALID')
    if conf['MODE'] == 'train' and to_norm:
        pool = tf.map_fn(lambda p: tf.nn.local_response_normalization(p, 4, bias=1.0, alpha=0.001 / 9.0, beta=0.75), pool)
    return pool

def conv3d(x, W):
    return tf.nn.conv3d(x, W, strides=[1, 1, 1, 1, 1], padding='SAME')

def combined_conv(inputs, kernel_size, out_channels, layer_name, activation_func=tf.nn.relu):
    _, depth, height, width, in_channels = inputs.get_shape().as_list()
    with tf.name_scope(layer_name):
        with tf.name_scope('sub_conv1'):
            with tf.name_scope('weights'):
                W_shape = [kernel_size, kernel_size, kernel_size, in_channels, out_channels]
                stddev = np.sqrt(2 / (kernel_size**3 * in_channels))
                W = weight_variable(W_shape, stddev)
                variable_summaries(W)
            with tf.name_scope('biases'):
                b = bias_variable([out_channels])
                variable_summaries(b)
            with tf.name_scope('activation'):
                z_1 = activation_func(conv3d(inputs, W) + b)
            tf.summary.image('activation', z_1[:, depth//2, ..., 1, None])
        with tf.name_scope('sub_conv2'):
            with tf.name_scope('weights'):
                W_shape = [kernel_size, kernel_size, kernel_size, out_channels, out_channels]
                stddev = np.sqrt(2 / (kernel_size**3 * out_channels))
                W = weight_variable(W_shape, stddev)
                variable_summaries(W)
            with tf.name_scope('biases'):
                b = bias_variable([out_channels])
                variable_summaries(b)
            with tf.name_scope('activation'):
                z_2 = activation_func(conv3d(z_1, W) + b)
            tf.summary.image('activation', z_2[:, depth//2, ..., 1, None])
        return z_2

def deconv3d(x, W, deconv_outshape, upsample_factor):
    return tf.nn.conv3d_transpose(x, W, deconv_outshape,
                strides=[1, upsample_factor, upsample_factor, upsample_factor, 1], padding='SAME')

def crop_and_concat(lhs, rhs):
    # Convert 5-d tensor to 4-d tensor.
    lhs_shape = tf.shape(lhs)
    rhs_shape = tf.shape(rhs)
    offsets = [0, (lhs_shape[1] - rhs_shape[1]) // 2, (lhs_shape[2] - rhs_shape[2]) // 2, (lhs_shape[3] - rhs_shape[3]) // 2, 0]
    size = [-1, rhs_shape[1], rhs_shape[2], rhs_shape[3], -1]
    cropped_lhs = tf.slice(lhs, offsets, size)
    cropped_lhs.set_shape(rhs.get_shape().as_list())
    return tf.concat([cropped_lhs, rhs], axis=4)

def combined_deconv(inputs, concat_inputs, kernel_size, out_channels, layer_name, activation_func=tf.nn.relu):
    batch_size, depth, height, width, input_channels = inputs.get_shape().as_list()
    with tf.name_scope(layer_name):
        with tf.name_scope('upsample'):
            with tf.name_scope('weights'):
                # Notice the order of inputs and outputs, which is required by `conv3d_transpose`
                W_shape = [kernel_size, kernel_size, kernel_size, out_channels, input_channels]
                stddev = np.sqrt(2 / (kernel_size**3 * input_channels))
                W = weight_variable(W_shape, stddev)
                variable_summaries(W)
            with tf.name_scope('biases'):
                b = bias_variable([out_channels])
                variable_summaries(b)
            with tf.name_scope('deconv'):
                deconv_outshape = [batch_size, depth*2, height*2, width*2, out_channels]
                up = activation_func(deconv3d(inputs, W, deconv_outshape, upsample_factor=2) + b)
            tf.summary.image('activation', up[:, depth // 2, ..., 1, None])
            with tf.name_scope('crop_and_concat'):
                glue = crop_and_concat(concat_inputs, up)
        return combined_conv(glue, kernel_size, out_channels, layer_name, activation_func)

def dice_coef(y_true, y_conv):
    """Compute dice among **positive** labels to avoid unbalance.

    Argument:
        y_true: [batch_size, depth, height, width, 1]
        y_conv: [batch_size, depth, height, width, 2]
    """
    y_true = tf.to_float(tf.reshape(y_true[..., 0], [-1]))
    y_conv = tf.to_float(tf.reshape(y_conv[..., 1], [-1]))
    intersection = tf.reduce_sum(y_conv * y_true)
    union = tf.reduce_sum(y_conv * y_conv) + tf.reduce_sum(y_true * y_true)
    dice = 2.0 * intersection / union
    return 1 - tf.clip_by_value(dice, 0, 1.0 - 1e-7)

def evaluation_metrics(y_true, y_conv):
    y_true = tf.to_float(y_true)
    y_conv = tf.to_float(y_conv)
    intersection = tf.reduce_sum(y_conv * y_true)
    union = tf.reduce_sum(y_conv) + tf.reduce_sum(y_true)
    dice = 2.0 * intersection / union * 100
    return dice

def show_slices(im_3d, indices=None):
    """ Function to display slices of 3-d image """

    plt.rcParams['image.cmap'] = 'gray'
    if indices is None:
        indices = np.array(im_3d.shape) // 2
    assert len(indices) == 3, """Except 3-d array,
                                 but receive %d-d array indexing.""" % len(indices)
    x_th, y_th, z_th = indices
    fig, axes = plt.subplots(1, 3)
    axes[0].imshow(im_3d[x_th, :, :])
    axes[1].imshow(im_3d[:, y_th, :])
    axes[2].imshow(im_3d[:, :, z_th])
    plt.suptitle("Center slices for spine image")
