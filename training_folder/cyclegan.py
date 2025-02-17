import os
import time

import matplotlib.pyplot as plt
import tensorflow as tf

# os.system("pip install git+https://github.com/tensorflow/examples.git")
# os.system("pip install tensorflow-datasets")

import tensorflow_datasets as tfds
from tensorflow_examples.models.pix2pix import pix2pix


AUTOTUNE = tf.data.experimental.AUTOTUNE
BUFFER_SIZE = 1000
BATCH_SIZE = 1
IMG_WIDTH = 256
IMG_HEIGHT = 256
OUTPUT_CHANNELS = 3
LAMBDA = 10
EPOCHS = 200
checkpoint_path = "../Outputs/checkpoints/train"
# checkpoint_path = "./checkpoints/train"


tfds.disable_progress_bar()

dataset, metadata = tfds.load(
    "cycle_gan/summer2winter_yosemite", with_info=True, as_supervised=True,
)

train_summer, train_winter = dataset["trainA"], dataset["trainB"]
test_summer, test_winter = dataset["testA"], dataset["testB"]


def random_jitter(image):
    # resizing to 286 x 286 x 3
    image = tf.image.resize(
        image, [286, 286], method=tf.image.ResizeMethod.NEAREST_NEIGHBOR
    )

    # randomly cropping to 256 x 256 x 3
    image = tf.image.random_crop(image, size=[IMG_HEIGHT, IMG_WIDTH, 3])

    # random mirroring
    image = tf.image.random_flip_left_right(image)

    return image


# normalize image to a range of [-1, 1]
def normalize(image):
    image = tf.cast(image, tf.float32)
    image = (image / 127.5) - 1
    return image


def preprocess_image_train(image, label):
    image = random_jitter(image)
    image = normalize(image)
    return image


def preprocess_image_test(image, label):
    image = normalize(image)
    return image


train_summer = (
    train_summer.map(preprocess_image_train, num_parallel_calls=AUTOTUNE)
    .cache()
    .shuffle(BUFFER_SIZE)
    .batch(1)
)

train_winter = (
    train_winter.map(preprocess_image_train, num_parallel_calls=AUTOTUNE)
    .cache()
    .shuffle(BUFFER_SIZE)
    .batch(1)
)

test_summer = (
    test_summer.map(preprocess_image_test, num_parallel_calls=AUTOTUNE)
    .cache()
    .shuffle(BUFFER_SIZE)
    .batch(1)
)

test_winter = (
    test_winter.map(preprocess_image_test, num_parallel_calls=AUTOTUNE)
    .cache()
    .shuffle(BUFFER_SIZE)
    .batch(1)
)

generator_g = pix2pix.unet_generator(OUTPUT_CHANNELS, norm_type="instancenorm")
generator_f = pix2pix.unet_generator(OUTPUT_CHANNELS, norm_type="instancenorm")

discriminator_x = pix2pix.discriminator(norm_type="instancenorm", target=False)
discriminator_y = pix2pix.discriminator(norm_type="instancenorm", target=False)


loss_obj = tf.keras.losses.BinaryCrossentropy(from_logits=True)


def discriminator_loss(real, generated):
    real_loss = loss_obj(tf.ones_like(real), real)

    generated_loss = loss_obj(tf.zeros_like(generated), generated)

    total_disc_loss = real_loss + generated_loss

    return total_disc_loss * 0.5


def generator_loss(generated):
    return loss_obj(tf.ones_like(generated), generated)


def calc_cycle_loss(real_image, cycled_image):
    loss1 = tf.reduce_mean(tf.abs(real_image - cycled_image))

    return LAMBDA * loss1


def identity_loss(real_image, same_image):
    loss = tf.reduce_mean(tf.abs(real_image - same_image))
    return LAMBDA * 0.5 * loss


generator_g_optimizer = tf.keras.optimizers.Adam(2e-4, beta_1=0.5)
generator_f_optimizer = tf.keras.optimizers.Adam(2e-4, beta_1=0.5)

discriminator_x_optimizer = tf.keras.optimizers.Adam(2e-4, beta_1=0.5)
discriminator_y_optimizer = tf.keras.optimizers.Adam(2e-4, beta_1=0.5)


ckpt = tf.train.Checkpoint(
    generator_g=generator_g,
    generator_f=generator_f,
    discriminator_x=discriminator_x,
    discriminator_y=discriminator_y,
    generator_g_optimizer=generator_g_optimizer,
    generator_f_optimizer=generator_f_optimizer,
    discriminator_x_optimizer=discriminator_x_optimizer,
    discriminator_y_optimizer=discriminator_y_optimizer,
)

ckpt_manager = tf.train.CheckpointManager(ckpt, checkpoint_path, max_to_keep=5)

# if a checkpoint exists, restore the latest checkpoint.
if ckpt_manager.latest_checkpoint:
    ckpt.restore(ckpt_manager.latest_checkpoint)
    print("Latest checkpoint restored!!")


def generate_images(model, test_input, fig_name):
    prediction = model(test_input)

    plt.figure(figsize=(12, 12))

    display_list = [test_input[0], prediction[0]]
    title = ["Input Image", "Predicted Image"]

    for i in range(2):
        plt.subplot(1, 2, i + 1)
        plt.title(title[i])
        # getting the pixel values between [0, 1] to plot it.
        plt.imshow(display_list[i] * 0.5 + 0.5)
        plt.axis("off")
    plt.savefig(fig_name)


@tf.function
def train_step(real_x, real_y, epoch):
    # persistent is set to True because the tape is used more than
    # once to calculate the gradients.
    with tf.GradientTape(persistent=True) as tape:
        # Generator G translates X -> Y
        # Generator F translates Y -> X.

        fake_y = generator_g(real_x, training=True)
        cycled_x = generator_f(fake_y, training=True)

        fake_x = generator_f(real_y, training=True)
        cycled_y = generator_g(fake_x, training=True)

        # same_x and same_y are used for identity loss.
        same_x = generator_f(real_x, training=True)
        same_y = generator_g(real_y, training=True)

        disc_real_x = discriminator_x(real_x, training=True)
        disc_real_y = discriminator_y(real_y, training=True)

        disc_fake_x = discriminator_x(fake_x, training=True)
        disc_fake_y = discriminator_y(fake_y, training=True)

        # calculate the loss
        gen_g_loss = generator_loss(disc_fake_y)
        gen_f_loss = generator_loss(disc_fake_x)

        total_cycle_loss = calc_cycle_loss(real_x, cycled_x) + calc_cycle_loss(
            real_y, cycled_y
        )

        # Total generator loss = adversarial loss + cycle loss
        total_gen_g_loss = gen_g_loss + total_cycle_loss + identity_loss(real_y, same_y)
        total_gen_f_loss = gen_f_loss + total_cycle_loss + identity_loss(real_x, same_x)

        print(
            f"{{'metric': 'Generator G Loss', 'value': {total_gen_g_loss}, 'epoch': {epoch}}}"
        )
        print(
            f"{{'metric': 'Generator F Loss', 'value': {total_gen_f_loss}, 'epoch': {epoch}}}"
        )

        disc_x_loss = discriminator_loss(disc_real_x, disc_fake_x)
        disc_y_loss = discriminator_loss(disc_real_y, disc_fake_y)

        print(
            f"{{'metric': 'Discriminator X Loss', 'value': {disc_x_loss}, 'epoch': {epoch}}}"
        )
        print(
            f"{{'metric': 'Discriminator Y Loss', 'value': {disc_y_loss}, 'epoch': {epoch}}}"
        )

    # Calculate the gradients for generator and discriminator
    generator_g_gradients = tape.gradient(
        total_gen_g_loss, generator_g.trainable_variables
    )
    generator_f_gradients = tape.gradient(
        total_gen_f_loss, generator_f.trainable_variables
    )

    discriminator_x_gradients = tape.gradient(
        disc_x_loss, discriminator_x.trainable_variables
    )
    discriminator_y_gradients = tape.gradient(
        disc_y_loss, discriminator_y.trainable_variables
    )

    # Apply the gradients to the optimizer
    generator_g_optimizer.apply_gradients(
        zip(generator_g_gradients, generator_g.trainable_variables)
    )

    generator_f_optimizer.apply_gradients(
        zip(generator_f_gradients, generator_f.trainable_variables)
    )

    discriminator_x_optimizer.apply_gradients(
        zip(discriminator_x_gradients, discriminator_x.trainable_variables)
    )

    discriminator_y_optimizer.apply_gradients(
        zip(discriminator_y_gradients, discriminator_y.trainable_variables)
    )


sample_summer_image = next(iter(train_summer))


def train_model():
    for epoch in range(EPOCHS):
        start = time.time()

        n = 0
        for image_x, image_y in tf.data.Dataset.zip((train_summer, train_winter)):
            train_step(image_x, image_y, epoch)
            if n % 10 == 0:
                print(".", end="")
            n += 1

        # Using a consistent image (sample_summer_image) so that the progress of the model
        # is clearly visible.
        generate_images(
            generator_g,
            sample_summer_image,
            "Summer to Winter image in epoch" + str(epoch),
        )

        if (epoch + 1) % 5 == 0:
            ckpt_save_path = ckpt_manager.save()
            print(
                "Saving checkpoint for epoch {} at {}".format(epoch + 1, ckpt_save_path)
            )

        print(
            "Time taken for epoch {} is {} sec\n".format(epoch + 1, time.time() - start)
        )


# train_model()

sample_summer = iter(test_summer.take(5))
sample_winter = iter(test_winter.take(5))
for index in range(5):
    generate_images(generator_g, next(sample_summer), "S2W Test Figure #" + str(index))
    generate_images(generator_f, next(sample_winter), "W2S Test Figure #" + str(index))
