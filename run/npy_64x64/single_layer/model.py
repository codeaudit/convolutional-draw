import os
import sys
import chainer
import uuid
import cupy
import chainer.functions as cf
from chainer.serializers import load_hdf5, save_hdf5
from chainer.backends import cuda

sys.path.append(os.path.join("..", "..", ".."))
import draw

from hyperparams import HyperParameters


class Model():
    def __init__(self, hyperparams: HyperParameters, snapshot_directory=None):
        assert isinstance(hyperparams, HyperParameters)
        self.generation_steps = hyperparams.generator_generation_steps
        self.hyperparams = hyperparams
        self.parameters = chainer.ChainList()

        self.generation_cores, self.generation_priors, self.generation_observation = self.build_generation_network(
            generation_steps=self.generation_steps,
            channels_chz=hyperparams.channels_chz)

        self.inference_cores, self.inference_posteriors, self.inference_downsampler = self.build_inference_network(
            generation_steps=self.generation_steps,
            channels_chz=hyperparams.channels_chz,
            channels_map_x=hyperparams.inference_channels_map_x)

        if snapshot_directory:
            try:
                filepath = os.path.join(snapshot_directory, self.filename)
                if os.path.exists(filepath) and os.path.isfile(filepath):
                    print("loading {}".format(filepath))
                    load_hdf5(filepath, self.parameters)
            except Exception as error:
                print(error)

    def build_generation_network(self, generation_steps, channels_chz):
        cores = []
        priors = []
        with self.parameters.init_scope():
            # LSTM core
            num_cores = 1 if self.hyperparams.generator_share_core else generation_steps
            for _ in range(num_cores):
                core = draw.nn.single_layer.generator.Core(channels_chz=channels_chz)
                cores.append(core)
                self.parameters.append(core)

            # z prior sampler
            num_priors = 1 if self.hyperparams.generator_share_prior else generation_steps
            for t in range(num_priors):
                prior = draw.nn.single_layer.generator.Prior(channels_z=channels_chz)
                priors.append(prior)
                self.parameters.append(prior)

            # observation sampler
            observation_distribution = draw.nn.single_layer.generator.ObservationDistribution(
            )
            self.parameters.append(observation_distribution)

        return cores, priors, observation_distribution

    def build_inference_network(self, generation_steps, channels_chz,
                                channels_map_x):
        cores = []
        posteriors = []
        with self.parameters.init_scope():
            num_cores = 1 if self.hyperparams.inference_share_core else generation_steps
            for t in range(num_cores):
                # LSTM core
                core = draw.nn.single_layer.inference.Core(channels_chz=channels_chz)
                cores.append(core)
                self.parameters.append(core)

            # z posterior sampler
            num_posteriors = 1 if self.hyperparams.inference_share_posterior else generation_steps
            for t in range(num_posteriors):
                posterior = draw.nn.single_layer.inference.Posterior(
                    channels_z=channels_chz)
                posteriors.append(posterior)
                self.parameters.append(posterior)

            # x downsampler
            downsampler = draw.nn.single_layer.inference.Downsampler(
                channels=channels_map_x)
            self.parameters.append(downsampler)

        return cores, posteriors, downsampler

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
        h0_g = xp.zeros(
            (
                batch_size,
                self.hyperparams.channels_chz,
            ) + self.hyperparams.chrz_size,
            dtype="float32")
        c0_g = xp.zeros(
            (
                batch_size,
                self.hyperparams.channels_chz,
            ) + self.hyperparams.chrz_size,
            dtype="float32")
        u0 = xp.zeros(
            (
                batch_size,
                self.hyperparams.generator_channels_u,
            ) + self.hyperparams.image_size,
            dtype="float32")
        h0_e = xp.zeros(
            (
                batch_size,
                self.hyperparams.channels_chz,
            ) + self.hyperparams.chrz_size,
            dtype="float32")
        c0_e = xp.zeros(
            (
                batch_size,
                self.hyperparams.channels_chz,
            ) + self.hyperparams.chrz_size,
            dtype="float32")
        return h0_g, c0_g, u0, h0_e, c0_e

    def get_generation_core(self, l):
        if self.hyperparams.generator_share_core:
            return self.generation_cores[0]
        return self.generation_cores[l]

    def get_generation_prior(self, l):
        if self.hyperparams.generator_share_prior:
            return self.generation_priors[0]
        return self.generation_priors[l]

    def get_inference_core(self, l):
        if self.hyperparams.inference_share_core:
            return self.inference_cores[0]
        return self.inference_cores[l]

    def get_inference_posterior(self, l):
        if self.hyperparams.inference_share_posterior:
            return self.inference_posteriors[0]
        return self.inference_posteriors[l]

    def generate_image(self, query_viewpoints, r, xp):
        batch_size = query_viewpoints.shape[0]
        h0_g, c0_g, u0, _, _ = self.generate_initial_state(batch_size, xp)
        hl_g = h0_g
        cl_g = c0_g
        ul_g = u0
        for l in range(self.generation_steps):
            core = self.get_generation_core(l)
            prior = self.get_generation_prior(l)
            zg_l = prior.sample_z(hl_g)
            next_h_g, next_c_g, next_u_g = core.forward_onestep(
                hl_g, cl_g, ul_g, zg_l, query_viewpoints, r)

            hl_g = next_h_g
            cl_g = next_c_g
            ul_g = next_u_g

        x = self.generation_observation.compute_mean_x(ul_g)
        return x.data

    def reconstruct_image(self, query_images, query_viewpoints, r, xp):
        batch_size = query_viewpoints.shape[0]
        h0_g, c0_g, u0, h0_e, c0_e = self.generate_initial_state(
            batch_size, xp)

        hl_e = h0_e
        cl_e = c0_e
        hl_g = h0_g
        cl_g = c0_g
        ul_e = u0

        xq = self.inference_downsampler.downsample(query_images)

        for l in range(self.generation_steps):
            inference_core = self.get_inference_core(l)
            inference_posterior = self.get_inference_posterior(l)
            generation_core = self.get_generation_core(l)

            he_next, ce_next = inference_core.forward_onestep(
                hl_g, hl_e, cl_e, xq, query_viewpoints, r)

            ze_l = inference_posterior.sample_z(hl_e)

            hg_next, cg_next, ue_next = generation_core.forward_onestep(
                hl_g, cl_g, ul_e, ze_l, query_viewpoints, r)

            hl_g = hg_next
            cl_g = cg_next
            ul_e = ue_next
            hl_e = he_next
            cl_e = ce_next

        x = self.generation_observation.compute_mean_x(ul_e)
        return x.data
