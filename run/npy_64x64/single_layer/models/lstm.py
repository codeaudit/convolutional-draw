import os
import sys
import chainer
import uuid
import cupy
import chainer.functions as cf
from chainer.serializers import load_hdf5, save_hdf5
from chainer.backends import cuda

sys.path.append(os.path.join("..", "..", "..", ".."))
import draw

from hyperparams import HyperParameters


class LSTMModel():
    def __init__(self, hyperparams: HyperParameters, snapshot_directory=None):
        assert isinstance(hyperparams, HyperParameters)
        self.generation_steps = hyperparams.generator_generation_steps
        self.hyperparams = hyperparams
        self.parameters = chainer.ChainList()

        self.generation_cores, self.generation_priors, self.generation_downsampler, self.generation_upsamplers, self.generation_final_upsampler = self.build_generation_network(
            generation_steps=self.generation_steps,
            chz_channels=hyperparams.chz_channels,
            downsampler_channels=hyperparams.generator_downsampler_channels,
            batchnorm_enabled=hyperparams.batch_normalization_enabled)

        self.inference_cores, self.inference_posteriors, self.inference_downsampler_x, self.inference_downsampler_diff_xr = self.build_inference_network(
            generation_steps=self.generation_steps,
            chz_channels=hyperparams.chz_channels,
            downsampler_channels=hyperparams.inference_downsampler_channels,
            batchnorm_enabled=hyperparams.batch_normalization_enabled)

        if snapshot_directory:
            try:
                filepath = os.path.join(snapshot_directory, self.filename)
                if os.path.exists(filepath) and os.path.isfile(filepath):
                    print("loading {}".format(filepath))
                    load_hdf5(filepath, self.parameters)
            except Exception as error:
                print(error)

    def build_generation_network(self, generation_steps, chz_channels,
                                 downsampler_channels, batchnorm_enabled):
        core_array = []
        prior_array = []
        upsampler_h_x_array = []
        with self.parameters.init_scope():
            # LSTM core
            num_cores = 1 if self.hyperparams.generator_share_core else generation_steps
            batchnorm_steps = generation_steps if self.hyperparams.generator_share_core else 1
            for _ in range(num_cores):
                core = draw.nn.single_layer.generator.LSTMCore(
                    chz_channels=chz_channels,
                    batchnorm_enabled=batchnorm_enabled,
                    batchnorm_steps=batchnorm_steps)
                core_array.append(core)
                self.parameters.append(core)

            # z prior sampler
            num_priors = 1 if self.hyperparams.generator_share_prior else generation_steps
            for _ in range(num_priors):
                prior = draw.nn.single_layer.generator.Prior(
                    channels_z=chz_channels)
                prior_array.append(prior)
                self.parameters.append(prior)

            # x downsampler
            downsampler_x_h = draw.nn.single_layer.downsampler.SingleLayeredConvDownsampler(
                channels=downsampler_channels)
            self.parameters.append(downsampler_x_h)

            # upsampler (h -> r)
            num_upsamplers = 1 if self.hyperparams.generator_share_upsampler else generation_steps - 1
            scale = 4
            for _ in range(num_upsamplers):
                upsampler = draw.nn.single_layer.upsampler.SubPixelConvolutionUpsampler(
                    channels=3 * scale**2, scale=scale)
                upsampler_h_x_array.append(upsampler)
                self.parameters.append(upsampler)

            final_upsampler = draw.nn.single_layer.upsampler.SubPixelConvolutionUpsampler(
                channels=6 * scale**2, scale=scale)
            upsampler_h_x_array.append(final_upsampler)
            self.parameters.append(final_upsampler)

        return core_array, prior_array, downsampler_x_h, upsampler_h_x_array, final_upsampler

    def build_inference_network(self, generation_steps, chz_channels,
                                downsampler_channels, batchnorm_enabled):
        core_array = []
        posteriors = []
        with self.parameters.init_scope():
            # LSTM core
            num_cores = 1 if self.hyperparams.inference_share_core else generation_steps
            batchnorm_steps = generation_steps if self.hyperparams.generator_share_core else 1
            for t in range(num_cores):
                core = draw.nn.single_layer.inference.LSTMCore(
                    chz_channels=chz_channels,
                    batchnorm_enabled=batchnorm_enabled,
                    batchnorm_steps=batchnorm_steps)
                core_array.append(core)
                self.parameters.append(core)

            # z posterior sampler
            num_posteriors = 1 if self.hyperparams.inference_share_posterior else generation_steps
            for t in range(num_posteriors):
                posterior = draw.nn.single_layer.inference.Posterior(
                    channels_z=chz_channels)
                posteriors.append(posterior)
                self.parameters.append(posterior)

            # x downsampler
            downsampler_x_h = draw.nn.single_layer.downsampler.SingleLayeredConvDownsampler(
                channels=downsampler_channels)
            downsampler_diff_xr_h = draw.nn.single_layer.downsampler.SingleLayeredConvDownsampler(
                channels=downsampler_channels)
            self.parameters.append(downsampler_x_h)
            self.parameters.append(downsampler_diff_xr_h)

        return core_array, posteriors, downsampler_x_h, downsampler_diff_xr_h

    def to_gpu(self):
        self.parameters.to_gpu()

    def cleargrads(self):
        self.parameters.cleargrads()

    @property
    def filename(self):
        return "model.hdf5"

    def serialize(self, path):
        self.serialize_parameter(path, self.filename, self.parameters)

    def serialize_parameter(self, path, filename, params):
        tmp_filename = str(uuid.uuid4())
        save_hdf5(os.path.join(path, tmp_filename), params)
        os.rename(
            os.path.join(path, tmp_filename), os.path.join(path, filename))

    def generate_initial_state(self, batch_size, xp):
        chrz_size = (32, 32)
        initial_h_gen = xp.zeros(
            (
                batch_size,
                self.hyperparams.chz_channels,
            ) + chrz_size,
            dtype="float32")
        initial_c_gen = xp.zeros(
            (
                batch_size,
                self.hyperparams.chz_channels,
            ) + chrz_size,
            dtype="float32")
        initial_r = xp.zeros(
            (
                batch_size,
                3,
            ) + self.hyperparams.image_size, dtype="float32")
        initial_h_enc = xp.zeros(
            (
                batch_size,
                self.hyperparams.chz_channels,
            ) + chrz_size,
            dtype="float32")
        initial_c_enc = xp.zeros(
            (
                batch_size,
                self.hyperparams.chz_channels,
            ) + chrz_size,
            dtype="float32")
        return initial_h_gen, initial_c_gen, initial_r, initial_h_enc, initial_c_enc

    def sample_image_at_each_step_from_posterior(self,
                                                 x,
                                                 zero_variance=False,
                                                 step_limit=None):
        if step_limit is None:
            step_limit = self.hyperparams.generator_generation_steps

        batch_size = x.shape[0]
        xp = cuda.get_array_module(x)
        h0_gen, c0_gen, initial_r, h0_enc, c0_enc = self.generate_initial_state(
            batch_size, xp)

        h_t_enc = h0_enc
        c_t_enc = c0_enc
        h_t_gen = h0_gen
        c_t_gen = c0_gen
        r_t = chainer.Variable(initial_r)
        downsampled_x = self.inference_downsampler_x.downsample(x)

        r_t_array = []

        for t in range(step_limit):
            is_final_step = t == step_limit - 1

            inference_core = self.get_inference_core(t)
            inference_posterior = self.get_inference_posterior(t)
            generation_core = self.get_generation_core(t)
            if is_final_step:
                generation_upsampler = self.generation_final_upsampler
            else:
                generation_upsampler = self.get_generation_upsampler(t)

            diff_xr = x - r_t
            downsampled_diff_xr = self.inference_downsampler_diff_xr.downsample(
                diff_xr)

            batchnorm_step = t if self.hyperparams.inference_share_core else 1
            h_next_enc, c_next_enc = inference_core.forward_onestep(
                h_t_gen, h_t_enc, c_t_enc, downsampled_x, downsampled_diff_xr,
                batchnorm_step)

            mean_z_q = inference_posterior.compute_mean_z(h_t_enc)
            ln_var_z_q = inference_posterior.compute_ln_var_z(h_t_enc)
            if zero_variance:
                z_t = mean_z_q
            else:
                z_t = cf.gaussian(mean_z_q, ln_var_z_q)

            batchnorm_step = t if self.hyperparams.generator_share_core else 1
            downsampled_r = self.generation_downsampler.downsample(r_t)
            h_next_gen, c_next_gen = generation_core.forward_onestep(
                h_t_gen, c_t_gen, z_t, downsampled_r, batchnorm_step)

            if is_final_step:
                x_param = generation_upsampler(h_next_gen)
                mu_x = x_param[:, :3] + r_t
                ln_var_x = x_param[:, 3:]
            else:
                h_t_gen = h_next_gen
                c_t_gen = c_next_gen
                h_t_enc = h_next_enc
                c_t_enc = c_next_enc

                r_t = r_t + generation_upsampler(h_next_gen)
                r_t_array.append(r_t.data)

        return r_t_array, (mu_x, ln_var_x)

    def sample_z_and_x_params_from_posterior(self, x):
        batch_size = x.shape[0]
        xp = cuda.get_array_module(x)
        h0_gen, c0_gen, initial_r, h0_enc, c0_enc = self.generate_initial_state(
            batch_size, xp)

        h_t_enc = h0_enc
        c_t_enc = c0_enc
        h_t_gen = h0_gen
        c_t_gen = c0_gen
        r_t = chainer.Variable(initial_r)
        downsampled_x = self.inference_downsampler_x.downsample(x)

        z_t_params_array = []
        r_t_array = []

        for t in range(self.generation_steps):
            is_final_step = t == self.generation_steps - 1

            inference_core = self.get_inference_core(t)
            inference_posterior = self.get_inference_posterior(t)
            generation_core = self.get_generation_core(t)
            generation_piror = self.get_generation_prior(t)

            if is_final_step:
                generation_upsampler = self.generation_final_upsampler
            else:
                generation_upsampler = self.get_generation_upsampler(t)

            diff_xr = x - r_t
            if self.hyperparams.no_backprop_diff_xr:
                diff_xr = diff_xr.data

            downsampled_diff_xr = self.inference_downsampler_diff_xr.downsample(
                diff_xr)

            batchnorm_step = t if self.hyperparams.inference_share_core else 1
            h_next_enc, c_next_enc = inference_core.forward_onestep(
                h_t_gen, h_t_enc, c_t_enc, downsampled_x, downsampled_diff_xr,
                batchnorm_step)

            mean_z_q = inference_posterior.compute_mean_z(h_t_enc)
            ln_var_z_q = inference_posterior.compute_ln_var_z(h_t_enc)
            z_t = cf.gaussian(mean_z_q, ln_var_z_q)

            mean_z_p = generation_piror.compute_mean_z(h_t_gen)
            ln_var_z_p = generation_piror.compute_ln_var_z(h_t_gen)

            batchnorm_step = t if self.hyperparams.generator_share_core else 1
            downsampled_r = self.generation_downsampler.downsample(r_t)
            h_next_gen, c_next_gen = generation_core.forward_onestep(
                h_t_gen, c_t_gen, z_t, downsampled_r, batchnorm_step)

            z_t_params_array.append((mean_z_q, ln_var_z_q, mean_z_p,
                                     ln_var_z_p))

            if is_final_step:
                x_param = generation_upsampler(h_next_gen)
                mu_x = x_param[:, :3] + r_t
                ln_var_x = x_param[:, 3:]
            else:
                r_t = r_t + generation_upsampler(h_next_gen)
                h_t_gen = h_next_gen
                c_t_gen = c_next_gen
                h_t_enc = h_next_enc
                c_t_enc = c_next_enc
                r_t_array.append(r_t)

        return z_t_params_array, (mu_x, ln_var_x), r_t_array

    def get_generation_core(self, l):
        if self.hyperparams.generator_share_core:
            return self.generation_cores[0]
        return self.generation_cores[l]

    def get_generation_prior(self, l):
        if self.hyperparams.generator_share_prior:
            return self.generation_priors[0]
        return self.generation_priors[l]

    def get_generation_upsampler(self, t):
        if self.hyperparams.generator_share_upsampler:
            return self.generation_upsamplers[0]
        return self.generation_upsamplers[t]

    def get_inference_core(self, l):
        if self.hyperparams.inference_share_core:
            return self.inference_cores[0]
        return self.inference_cores[l]

    def get_inference_posterior(self, l):
        if self.hyperparams.inference_share_posterior:
            return self.inference_posteriors[0]
        return self.inference_posteriors[l]

    def sample_image_at_each_step_from_prior(self, batch_size, xp):
        h0_gen, c0_gen, initial_r, _, _ = self.generate_initial_state(
            batch_size, xp)
        h_t_gen = h0_gen
        c_t_gen = c0_gen
        r_t = chainer.Variable(initial_r)
        r_t_array = []

        for t in range(self.generation_steps):
            is_final_step = t == self.generation_steps - 1

            generation_core = self.get_generation_core(t)
            generation_piror = self.get_generation_prior(t)

            if is_final_step:
                generation_upsampler = self.generation_final_upsampler
            else:
                generation_upsampler = self.get_generation_upsampler(t)

            batchnorm_step = t if self.hyperparams.generator_share_core else 1

            mean_z_q = generation_piror.compute_mean_z(h_t_gen)
            ln_var_z_q = generation_piror.compute_ln_var_z(h_t_gen)
            z_t = cf.gaussian(mean_z_q, ln_var_z_q)

            downsampled_r = self.generation_downsampler.downsample(r_t)
            h_next_gen, c_next_gen = generation_core.forward_onestep(
                h_t_gen, c_t_gen, z_t, downsampled_r, batchnorm_step)

            if is_final_step:
                x_param = generation_upsampler(h_next_gen)
                mu_x = x_param[:, :3] + r_t
                ln_var_x = x_param[:, 3:]
            else:
                h_t_gen = h_next_gen
                c_t_gen = c_next_gen
                r_t = r_t + generation_upsampler(h_next_gen)
                r_t_array.append(r_t.data)

        return r_t_array, (mu_x, ln_var_x)
