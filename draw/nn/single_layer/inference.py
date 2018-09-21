import math

import chainer
import chainer.functions as cf
import chainer.links as L
import cupy
from chainer.backends import cuda
from chainer.initializers import HeNormal


class Core(chainer.Chain):
    def __init__(self, channels_chz):
        super().__init__()
        with self.init_scope():
            self.lstm_tanh = L.Convolution2D(
                None,
                channels_chz,
                ksize=5,
                stride=1,
                pad=2,
                initialW=HeNormal(0.1))
            self.lstm_i = L.Convolution2D(
                None,
                channels_chz,
                ksize=5,
                stride=1,
                pad=2,
                initialW=HeNormal(0.1))
            self.lstm_f = L.Convolution2D(
                None,
                channels_chz,
                ksize=5,
                stride=1,
                pad=2,
                initialW=HeNormal(0.1))
            self.lstm_o = L.Convolution2D(
                None,
                channels_chz,
                ksize=5,
                stride=1,
                pad=2,
                initialW=HeNormal(0.1))

    def forward_onestep(self, prev_hg, prev_he, prev_ce, x, diff_xr):
        xp = cuda.get_array_module(v)
        lstm_in = cf.concat((prev_he, prev_hg, x, diff_xr), axis=1)
        forget_gate = cf.sigmoid(self.lstm_f(lstm_in))
        input_gate = cf.sigmoid(self.lstm_i(lstm_in))
        next_c = forget_gate * prev_ce + input_gate * cf.tanh(
            self.lstm_tanh(lstm_in))
        next_h = cf.sigmoid(self.lstm_o(lstm_in)) * cf.tanh(next_c)
        return next_h, next_c


class Posterior(chainer.Chain):
    def __init__(self, channels_z):
        super().__init__()
        with self.init_scope():
            self.mean_z = L.Convolution2D(
                None,
                channels_z,
                ksize=5,
                stride=1,
                pad=2,
                initialW=HeNormal(0.1))
            self.ln_var_z = L.Convolution2D(
                None,
                channels_z,
                ksize=5,
                stride=1,
                pad=2,
                initialW=HeNormal(0.1))

    def compute_mean_z(self, h):
        return self.mean_z(h)

    def compute_ln_var_z(self, h):
        return self.ln_var_z(h)

    def sample_z(self, h):
        mean = self.compute_mean_z(h)
        ln_var = self.compute_ln_var_z(h)
        return cf.gaussian(mean, ln_var)


class _Downsampler(chainer.Chain):
    def __init__(self, channels):
        super().__init__()
        with self.init_scope():
            self.conv_1 = L.Convolution2D(
                None,
                channels,
                ksize=2,
                stride=2,
                pad=0,
                initialW=HeNormal(0.1))
            self.conv_2 = L.Convolution2D(
                None,
                channels,
                ksize=3,
                pad=1,
                stride=1,
                initialW=HeNormal(0.1))
            self.conv_3 = L.Convolution2D(
                None,
                channels,
                ksize=2,
                stride=2,
                pad=0,
                initialW=HeNormal(0.1))

    def downsample(self, x):
        x = cf.relu(self.conv_1(x))
        x = cf.relu(self.conv_2(x))
        x = self.conv_3(x)
        return x


class Downsampler(chainer.Chain):
    def __init__(self, channels):
        super().__init__()
        with self.init_scope():
            self.conv1_1 = L.Convolution2D(
                None,
                channels,
                ksize=2,
                pad=0,
                stride=2,
                initialW=HeNormal(0.1))
            self.conv1_2 = L.Convolution2D(
                None,
                channels // 2,
                ksize=3,
                pad=1,
                stride=1,
                initialW=HeNormal(0.1))
            self.conv1_res = L.Convolution2D(
                None,
                channels,
                ksize=2,
                pad=0,
                stride=2,
                initialW=HeNormal(0.1))
            self.conv1_3 = L.Convolution2D(
                None,
                channels,
                ksize=2,
                pad=0,
                stride=2,
                initialW=HeNormal(0.1))
            self.conv2_1 = L.Convolution2D(
                None,
                channels // 2,
                ksize=3,
                pad=1,
                stride=1,
                initialW=HeNormal(0.1))
            self.conv2_2 = L.Convolution2D(
                None,
                channels,
                ksize=3,
                pad=1,
                stride=1,
                initialW=HeNormal(0.1))
            self.conv2_res = L.Convolution2D(
                None,
                channels,
                ksize=3,
                pad=1,
                stride=1,
                initialW=HeNormal(0.1))
            self.conv2_3 = L.Convolution2D(
                None,
                channels,
                ksize=1,
                pad=0,
                stride=1,
                initialW=HeNormal(0.1))

    def downsample(self, x):
        resnet_in = cf.relu(self.conv1_1(x))
        residual = cf.relu(self.conv1_res(resnet_in))
        out = cf.relu(self.conv1_2(resnet_in))
        resnet_in = cf.relu(self.conv1_3(out)) + residual
        residual = cf.relu(self.conv2_res(resnet_in))
        out = cf.relu(self.conv2_1(resnet_in))
        out = cf.relu(self.conv2_2(out)) + residual
        out = self.conv2_3(out)
        return out
