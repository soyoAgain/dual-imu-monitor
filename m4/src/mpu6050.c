#include <stdint.h>
#include "stm32mp1xx.h"

#define SDA_PIN 12U
#define SCL_PIN 13U
#define SDA_MASK (1UL << SDA_PIN)
#define SCL_MASK (1UL << SCL_PIN)
#define WHO_AM_I_REGISTER 0x75U
#define STATUS_MAGIC 0x4D505536UL
#define STATUS_VERSION 2U
#define M4_CORE_CLOCK_HZ 208877930UL
#define SAMPLE_PERIOD_MS 20U

struct remoteproc_resource_table
{
  uint32_t version;
  uint32_t entries;
  uint32_t reserved[2];
};

struct mpu6050_status
{
  uint32_t magic;
  uint32_t version;
  uint32_t sequence;
  uint32_t address;
  uint32_t who_am_i;
  uint32_t idle_lines;
  uint32_t scheduled_ms;
  uint32_t timestamp_ms;
  uint32_t period_ms;
  uint32_t overruns;
  uint32_t i2c_errors;
  int32_t accel_x;
  int32_t accel_y;
  int32_t accel_z;
  int32_t temperature;
  int32_t gyro_x;
  int32_t gyro_y;
  int32_t gyro_z;
  uint32_t last_error;
};

__attribute__((section(".resource_table"), used))
const struct remoteproc_resource_table resource_table = {
  .version = 1U,
  .entries = 0U,
  .reserved = {0U, 0U},
};

__attribute__((section(".shared_status"), used))
volatile struct mpu6050_status status;
static volatile uint32_t milliseconds;

void SysTick_Handler(void)
{
  milliseconds++;
}

static void i2c_delay(void)
{
  for (volatile uint32_t count = 0U; count < 600U; ++count)
  {
    __asm volatile ("nop");
  }
}

static void lines_high(uint32_t mask)
{
  GPIOB->BSRR = mask;
  i2c_delay();
}

static void lines_low(uint32_t mask)
{
  GPIOB->BSRR = mask << 16U;
  i2c_delay();
}

static uint32_t read_line(uint32_t mask)
{
  return (GPIOB->IDR & mask) != 0U;
}

static void i2c_start(void)
{
  lines_high(SDA_MASK | SCL_MASK);
  lines_low(SDA_MASK);
  lines_low(SCL_MASK);
}

static void i2c_stop(void)
{
  lines_low(SDA_MASK);
  lines_high(SCL_MASK);
  lines_high(SDA_MASK);
}

static uint32_t i2c_write_byte(uint8_t value)
{
  for (uint32_t bit = 0U; bit < 8U; ++bit)
  {
    if ((value & 0x80U) != 0U)
    {
      lines_high(SDA_MASK);
    }
    else
    {
      lines_low(SDA_MASK);
    }

    lines_high(SCL_MASK);
    lines_low(SCL_MASK);
    value <<= 1U;
  }

  lines_high(SDA_MASK);
  lines_high(SCL_MASK);
  const uint32_t acknowledged = read_line(SDA_MASK) == 0U;
  lines_low(SCL_MASK);
  return acknowledged;
}

static uint8_t i2c_read_byte(uint32_t acknowledge)
{
  uint8_t value = 0U;
  lines_high(SDA_MASK);

  for (uint32_t bit = 0U; bit < 8U; ++bit)
  {
    lines_high(SCL_MASK);
    value = (uint8_t)((value << 1U) | read_line(SDA_MASK));
    lines_low(SCL_MASK);
  }

  if (acknowledge != 0U)
  {
    lines_low(SDA_MASK);
  }
  else
  {
    lines_high(SDA_MASK);
  }
  lines_high(SCL_MASK);
  lines_low(SCL_MASK);
  lines_high(SDA_MASK);
  return value;
}

static uint32_t read_who_am_i(uint8_t address, uint8_t *value)
{
  i2c_start();
  if (i2c_write_byte((uint8_t)(address << 1U)) == 0U)
  {
    i2c_stop();
    return 1U;
  }
  if (i2c_write_byte(WHO_AM_I_REGISTER) == 0U)
  {
    i2c_stop();
    return 2U;
  }

  i2c_start();
  if (i2c_write_byte((uint8_t)((address << 1U) | 1U)) == 0U)
  {
    i2c_stop();
    return 3U;
  }

  *value = i2c_read_byte(0U);
  i2c_stop();
  return 0U;
}

static uint32_t write_register(uint8_t address, uint8_t reg, uint8_t value)
{
  i2c_start();
  if (i2c_write_byte((uint8_t)(address << 1U)) == 0U)
  {
    i2c_stop();
    return 1U;
  }
  if (i2c_write_byte(reg) == 0U)
  {
    i2c_stop();
    return 2U;
  }
  if (i2c_write_byte(value) == 0U)
  {
    i2c_stop();
    return 3U;
  }
  i2c_stop();
  return 0U;
}

static uint32_t read_registers(uint8_t address, uint8_t first_register,
                               uint8_t *data, uint32_t length)
{
  i2c_start();
  if (i2c_write_byte((uint8_t)(address << 1U)) == 0U)
  {
    i2c_stop();
    return 1U;
  }
  if (i2c_write_byte(first_register) == 0U)
  {
    i2c_stop();
    return 2U;
  }

  i2c_start();
  if (i2c_write_byte((uint8_t)((address << 1U) | 1U)) == 0U)
  {
    i2c_stop();
    return 3U;
  }

  for (uint32_t index = 0U; index < length; ++index)
  {
    data[index] = i2c_read_byte(index + 1U < length);
  }
  i2c_stop();
  return 0U;
}

static int32_t signed_word(const uint8_t *bytes)
{
  const uint16_t value = ((uint16_t)bytes[0] << 8U) | bytes[1];
  return (int32_t)(int16_t)value;
}

static void systick_init(void)
{
  SysTick->LOAD = ((M4_CORE_CLOCK_HZ + 500U) / 1000U) - 1U;
  SysTick->VAL = 0U;
  SysTick->CTRL = SysTick_CTRL_CLKSOURCE_Msk |
                  SysTick_CTRL_TICKINT_Msk |
                  SysTick_CTRL_ENABLE_Msk;
}

static uint32_t gpio_read_stable(volatile uint32_t *reg)
{
  /* 使能 GPIO 时钟后总线需要若干周期才可访问。若立即读-改-写，读回 0
     会把其他引脚的 MODER/OSPEEDR/PUPDR 清零，曾导致以太网失联。 */
  uint32_t value = *reg;
  for (uint32_t attempt = 0U; attempt < 100000U && value == 0U; ++attempt)
  {
    value = *reg;
  }
  return value;
}

static void gpio_i2c_init(void)
{
  RCC->MC_AHB4ENSETR = RCC_MC_AHB4ENSETR_GPIOBEN;
  (void)RCC->MC_AHB4ENSETR;

  const uint32_t moder = gpio_read_stable(&GPIOB->MODER);

  const uint32_t mode_mask = (3UL << (SDA_PIN * 2U)) |
                             (3UL << (SCL_PIN * 2U));
  const uint32_t output_mode = (1UL << (SDA_PIN * 2U)) |
                               (1UL << (SCL_PIN * 2U));

  GPIOB->MODER = (moder & ~mode_mask) | output_mode;
  GPIOB->OTYPER |= SDA_MASK | SCL_MASK;
  GPIOB->OSPEEDR &= ~mode_mask;
  GPIOB->PUPDR &= ~mode_mask;
  lines_high(SDA_MASK | SCL_MASK);

  /* remoteproc 可能在一次传输中途停止旧固件：发送 9 个时钟并产生 STOP。 */
  for (uint32_t pulse = 0U; pulse < 9U; ++pulse)
  {
    lines_low(SCL_MASK);
    lines_high(SCL_MASK);
  }
  i2c_stop();
}

int main(void)
{
  uint8_t who_am_i = 0U;
  uint8_t sample[14];
  uint32_t address = 0x68U;

  gpio_i2c_init();

  status.magic = STATUS_MAGIC;
  status.version = STATUS_VERSION;
  status.sequence = 0U;
  status.address = 0U;
  status.who_am_i = 0U;
  status.idle_lines = (read_line(SDA_MASK) << 0U) |
                      (read_line(SCL_MASK) << 1U);
  status.period_ms = SAMPLE_PERIOD_MS;
  status.overruns = 0U;
  status.i2c_errors = 0U;
  status.last_error = read_who_am_i(address, &who_am_i);

  if (status.last_error != 0U)
  {
    address = 0x69U;
    status.last_error = read_who_am_i(address, &who_am_i);
  }

  status.address = address;
  status.who_am_i = who_am_i;

  if (status.last_error == 0U)
  {
    status.last_error |= write_register((uint8_t)address, 0x6BU, 0x00U);
    status.last_error |= write_register((uint8_t)address, 0x1AU, 0x03U);
    status.last_error |= write_register((uint8_t)address, 0x19U, 0x13U);
    status.last_error |= write_register((uint8_t)address, 0x1BU, 0x00U);
    status.last_error |= write_register((uint8_t)address, 0x1CU, 0x00U);
  }

  systick_init();
  uint32_t next_sample_ms = milliseconds + SAMPLE_PERIOD_MS;

  for (;;)
  {
    while ((int32_t)(milliseconds - next_sample_ms) < 0)
    {
      __WFI();
    }

    const uint32_t timestamp_ms = milliseconds;
    if ((int32_t)(timestamp_ms - next_sample_ms) >= (int32_t)SAMPLE_PERIOD_MS)
    {
      status.overruns++;
      next_sample_ms = timestamp_ms;
    }

    uint32_t error;
    uint32_t sample_valid = 0U;
    if (status.who_am_i != 0x68U && status.who_am_i != 0x69U)
    {
      address = 0x68U;
      error = read_who_am_i((uint8_t)address, &who_am_i);
      if (error != 0U)
      {
        address = 0x69U;
        error = read_who_am_i((uint8_t)address, &who_am_i);
      }
      status.address = address;
      status.who_am_i = who_am_i;
      if (error == 0U)
      {
        error |= write_register((uint8_t)address, 0x6BU, 0x00U);
        error |= write_register((uint8_t)address, 0x1AU, 0x03U);
        error |= write_register((uint8_t)address, 0x19U, 0x13U);
        error |= write_register((uint8_t)address, 0x1BU, 0x00U);
        error |= write_register((uint8_t)address, 0x1CU, 0x00U);
      }
    }
    else
    {
      error = read_registers((uint8_t)address, 0x3BU,
                             sample, sizeof(sample));
      sample_valid = error == 0U;
    }
    if (sample_valid != 0U)
    {
      status.accel_x = signed_word(&sample[0]);
      status.accel_y = signed_word(&sample[2]);
      status.accel_z = signed_word(&sample[4]);
      status.temperature = signed_word(&sample[6]);
      status.gyro_x = signed_word(&sample[8]);
      status.gyro_y = signed_word(&sample[10]);
      status.gyro_z = signed_word(&sample[12]);
    }
    else
    {
      status.i2c_errors++;
    }

    status.scheduled_ms = next_sample_ms;
    status.timestamp_ms = timestamp_ms;
    status.last_error = error;
    __DMB();
    status.sequence++;
    next_sample_ms += SAMPLE_PERIOD_MS;
  }
}
