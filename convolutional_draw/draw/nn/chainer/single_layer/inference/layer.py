import chainer
import chainer.functions as cf
import cupy
import math

from .... import base
from .parameters import Parameters


class Layer(base.single_layer.inference.Layer):
    def __init__(self, params):
        assert isinstance(params, Parameters)
        self.params = params

    def forward_onestep(self, prec_ce, x, error, prev_he, prev_hd):
        x = self.params.conv_x_concat(x)
        error = self.params.conv_error_concat(error)

        lstm_in = cf.concat((x, error, prev_he, prev_hd), axis=1)
        forget_gate = cf.sigmoid(self.params.lstm_f(lstm_in))
        input_gate = cf.sigmoid(self.params.lstm_i(lstm_in))
        next_c = forget_gate * prec_ce + input_gate * cf.tanh(
            self.params.lstm_tanh(lstm_in))
        next_h = cf.sigmoid(self.params.lstm_o(lstm_in)) * cf.tanh(next_c)
        return next_h, next_c

    def compute_mean_z(self, h):
        return self.params.mean_z(h)

    def compute_ln_var_z(self, h):
        return self.params.ln_var_z(h)

    def sample_z(self, h):
        mean = self.compute_mean_z(h)
        ln_var = self.compute_ln_var_z(h)
        return cf.gaussian(mean, ln_var)