import argparse
import math
import os
import random
import sys

import chainer
import chainer.functions as cf
import matplotlib.pyplot as plt
import numpy as np
import cupy as cp
from chainer.backends import cuda
from PIL import Image

sys.path.append(os.path.join("..", "..", ".."))
import draw
from hyperparams import HyperParameters
from model import Model
from optimizer import AdamOptimizer, SGDOptimizer, MomentumSGDOptimizer


def printr(string):
    sys.stdout.write(string)
    sys.stdout.write("\r")


def to_gpu(array):
    if cuda.get_array_module(array) is np:
        return cuda.to_gpu(array)
    return array


def to_cpu(array):
    if cuda.get_array_module(array) is cp:
        return cuda.to_cpu(array)
    return array


def make_uint8(x):
    x = to_cpu(x)
    if x.shape[0] == 3:
        x = x.transpose(1, 2, 0)
    return np.uint8(np.clip((x + 1) * 0.5 * 255, 0, 255))


def main():
    try:
        os.mkdir(args.snapshot_directory)
    except:
        pass

    images = []
    files = os.listdir(args.dataset_path)
    for filename in files:
        image = np.load(os.path.join(args.dataset_path, filename))
        image = image / 255 * 2.0 - 1.0
        images.append(image)

    images = np.vstack(images)
    images = images.transpose((0, 3, 1, 2)).astype(np.float32)
    train_dev_split = 0.9
    num_images = images.shape[0]
    num_train_images = int(num_images * train_dev_split)
    num_dev_images = num_images - num_train_images
    images_train = images[:args.batch_size]
    images_dev = images[args.batch_size:]

    xp = np
    using_gpu = args.gpu_device >= 0
    if using_gpu:
        cuda.get_device(args.gpu_device).use()
        xp = cp

    hyperparams = HyperParameters()
    hyperparams.generator_share_core = args.generator_share_core
    hyperparams.generator_share_prior = args.generator_share_prior
    hyperparams.generator_generation_steps = args.generation_steps
    hyperparams.inference_share_core = args.inference_share_core
    hyperparams.inference_share_posterior = args.inference_share_posterior
    hyperparams.layer_normalization_enabled = args.layer_normalization
    hyperparams.pixel_n = args.pixel_n
    hyperparams.chz_channels = args.chz_channels
    hyperparams.inference_channels_downsampler_x = args.channels_downsampler_x
    hyperparams.pixel_sigma_i = args.initial_pixel_sigma
    hyperparams.pixel_sigma_f = args.final_pixel_sigma
    hyperparams.chrz_size = (32, 32)
    hyperparams.save(args.snapshot_directory)
    hyperparams.print()

    model = Model(hyperparams, snapshot_directory=args.snapshot_directory)
    if using_gpu:
        model.to_gpu()

    optimizer = AdamOptimizer(
        model.parameters, lr_i=args.initial_lr, lr_f=args.final_lr)
    optimizer.print()

    sigma_t = hyperparams.pixel_sigma_i
    pixel_var = xp.full(
        (args.batch_size, 3) + hyperparams.image_size,
        sigma_t**2,
        dtype="float32")
    pixel_ln_var = xp.full(
        (args.batch_size, 3) + hyperparams.image_size,
        math.log(sigma_t**2),
        dtype="float32")
    num_pixels = images.shape[1] * images.shape[2] * images.shape[3]

    figure = plt.figure(figsize=(20, 4))
    axis_1 = figure.add_subplot(1, 5, 1)
    axis_2 = figure.add_subplot(1, 5, 2)
    axis_3 = figure.add_subplot(1, 5, 3)
    axis_4 = figure.add_subplot(1, 5, 4)
    axis_5 = figure.add_subplot(1, 5, 5)

    for iteration in range(args.training_steps):
        x = to_gpu(images_train)
        loss_kld = 0

        z_t_params_array, r_final = model.generate_z_params_and_x_from_posterior(
            x)
        for params in z_t_params_array:
            mean_z_q, ln_var_z_q, mean_z_p, ln_var_z_p = params
            kld = draw.nn.functions.gaussian_kl_divergence(
                mean_z_q, ln_var_z_q, mean_z_p, ln_var_z_p)
            loss_kld += cf.sum(kld)

        mean_x_enc = r_final
        negative_log_likelihood = draw.nn.functions.gaussian_negative_log_likelihood(
            x, mean_x_enc, pixel_var, pixel_ln_var)
        loss_nll = cf.sum(negative_log_likelihood)
        loss_mse = cf.mean_squared_error(mean_x_enc, x)

        loss_nll /= args.batch_size
        loss_kld /= args.batch_size
        loss = loss_nll + loss_kld
        loss = loss_nll
        model.cleargrads()
        loss.backward()
        optimizer.update(iteration)

        sf = hyperparams.pixel_sigma_f
        si = hyperparams.pixel_sigma_i
        sigma_t = max(sf + (si - sf) * (1.0 - iteration / hyperparams.pixel_n),
                      sf)

        pixel_var[...] = sigma_t**2
        pixel_ln_var[...] = math.log(sigma_t**2)

        model.serialize(args.snapshot_directory)
        print(
            "\033[2KIteration {} - loss: nll_per_pixel: {:.6f} - mse: {:.6f} - kld: {:.6f} - lr: {:.4e} - sigma_t: {:.6f}".
            format(iteration + 1,
                   float(loss_nll.data) / num_pixels, float(loss_mse.data),
                   float(loss_kld.data), optimizer.learning_rate, sigma_t))

        if iteration % 10 == 0:
            axis_1.imshow(make_uint8(x[0]))
            axis_2.imshow(make_uint8(mean_x_enc.data[0]))

            x_dev = images_dev[random.choice(range(num_dev_images))]
            axis_3.imshow(make_uint8(x_dev))

            with chainer.using_config("train", False), chainer.using_config(
                    "enable_backprop", False):
                x_dev = to_gpu(x_dev)[None, ...]
                _, r_final = model.generate_z_params_and_x_from_posterior(
                    x_dev)
                mean_x_enc = r_final
                axis_4.imshow(make_uint8(mean_x_enc.data[0]))

                mean_x_d = model.generate_image(batch_size=1, xp=xp)
                axis_5.imshow(make_uint8(mean_x_d[0]))

            plt.pause(0.01)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-path", "-dataset", type=str, required=True)
    parser.add_argument(
        "--snapshot-directory", "-snapshot", type=str, default="snapshot")
    parser.add_argument("--batch-size", "-b", type=int, default=36)
    parser.add_argument("--gpu-device", "-gpu", type=int, default=0)
    parser.add_argument("--training-steps", type=int, default=10**6)
    parser.add_argument("--generation-steps", "-gsteps", type=int, default=8)
    parser.add_argument(
        "--initial-lr", "-mu-i", type=float, default=5.0 * 1e-4)
    parser.add_argument("--final-lr", "-mu-f", type=float, default=5.0 * 1e-5)
    parser.add_argument(
        "--initial-pixel-sigma", "-ps-i", type=float, default=2.0)
    parser.add_argument(
        "--final-pixel-sigma", "-ps-f", type=float, default=0.7)
    parser.add_argument("--pixel-n", "-pn", type=int, default=2 * 10**5)
    parser.add_argument("--channels-chz", "-cz", type=int, default=64)
    parser.add_argument("--channels-downsampler-x", "-cx", type=int, default=12)
    parser.add_argument(
        "--generator-share-core", "-g-share-core", action="store_true")
    parser.add_argument(
        "--generator-share-prior", "-g-share-prior", action="store_true")
    parser.add_argument(
        "--inference-share-core", "-i-share-core", action="store_true")
    parser.add_argument(
        "--inference-share-posterior",
        "-i-share-posterior",
        action="store_true")
    parser.add_argument("--layer-normalization", "-ln", action="store_true")
    args = parser.parse_args()
    main()
