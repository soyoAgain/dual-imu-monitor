#include <stdint.h>
#include <string.h>
#include "main.h"
#include "virt_uart.h"

#define SDA_PIN 12U
#define SCL_PIN 13U
#define SDA_MASK (1UL << SDA_PIN)
#define SCL_MASK (1UL << SCL_PIN)
#define WHO_AM_I_REGISTER 0x75U
#define M4_CORE_CLOCK_HZ 208877930UL
#define SAMPLE_PERIOD_MS 20U
#define PACKET_MAGIC 0x31554D49UL /* ASCII "IMU1" in little-endian memory. */
/* 传感器掉电重插后寄存器复位、读数全为 0；连续多帧全 0 时自动重新初始化。 */
#define SENSOR_REINIT_ZERO_FRAMES 10U
#define SENSOR_REINIT_COOLDOWN_FRAMES 25U

typedef struct __attribute__((packed))
{
  uint32_t magic;
  uint16_t version;
  uint16_t size;
  uint32_t sequence;
  uint32_t timestamp_ms;
  int16_t accel_x;
  int16_t accel_y;
  int16_t accel_z;
  int16_t temperature;
  int16_t gyro_x;
  int16_t gyro_y;
  int16_t gyro_z;
  uint16_t i2c_errors;
  uint16_t tx_errors;
  uint32_t checksum;
} sample_packet_t;

IPCC_HandleTypeDef hipcc;
static VIRT_UART_HandleTypeDef huart0;
static volatile uint32_t stream_enabled;
#if defined(M4_GPIO_CONFIG_ONLY) || M4_MAIN_SAMPLE_LIMIT > 0
/* Read-only diagnostic results for Linux /dev/mem inspection. */
volatile uint32_t m4_diagnostic_status[5]; /* stage, ID, ID error, samples, errors */
#endif

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
  if (i2c_write_byte((uint8_t)(address << 1U)) == 0U ||
      i2c_write_byte(reg) == 0U || i2c_write_byte(value) == 0U)
  {
    i2c_stop();
    return 1U;
  }
  i2c_stop();
  return 0U;
}

static uint32_t read_registers(uint8_t address, uint8_t first_register,
                               uint8_t *data, uint32_t length)
{
  i2c_start();
  if (i2c_write_byte((uint8_t)(address << 1U)) == 0U ||
      i2c_write_byte(first_register) == 0U)
  {
    i2c_stop();
    return 1U;
  }
  i2c_start();
  if (i2c_write_byte((uint8_t)((address << 1U) | 1U)) == 0U)
  {
    i2c_stop();
    return 2U;
  }
  for (uint32_t index = 0U; index < length; ++index)
  {
    data[index] = i2c_read_byte(index + 1U < length);
  }
  i2c_stop();
  return 0U;
}

static int16_t signed_word(const uint8_t *bytes)
{
  return (int16_t)(((uint16_t)bytes[0] << 8U) | bytes[1]);
}

static uint32_t sensor_configure(uint8_t address)
{
  /* 唤醒并配置 MPU6050；传感器掉电重插后需要重新执行。 */
  uint32_t error = 0U;
  error |= write_register(address, 0x6BU, 0x00U);
  error |= write_register(address, 0x1AU, 0x03U);
  error |= write_register(address, 0x19U, 0x13U);
  error |= write_register(address, 0x1BU, 0x00U);
  error |= write_register(address, 0x1CU, 0x00U);
  return error;
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

#ifdef M4_GPIO_CONFIG_ONLY
  m4_diagnostic_status[0] = 1U;
  const uint32_t saved_mode = moder;
  const uint32_t saved_type = GPIOB->OTYPER;
  const uint32_t saved_speed = GPIOB->OSPEEDR;
  const uint32_t saved_pull = GPIOB->PUPDR;
  const uint32_t saved_output = GPIOB->ODR;
#endif
  const uint32_t mode_mask = (3UL << (SDA_PIN * 2U)) |
                             (3UL << (SCL_PIN * 2U));
  const uint32_t output_mode = (1UL << (SDA_PIN * 2U)) |
                               (1UL << (SCL_PIN * 2U));
  GPIOB->MODER = (moder & ~mode_mask) | output_mode;
  GPIOB->OTYPER |= SDA_MASK | SCL_MASK;
  GPIOB->OSPEEDR &= ~mode_mask;
  GPIOB->PUPDR &= ~mode_mask;
  lines_high(SDA_MASK | SCL_MASK);
#ifdef M4_GPIO_CONFIG_ONLY
#ifdef M4_I2C_RECOVERY_ONLY
  for (uint32_t pulse = 0U; pulse < 9U; ++pulse)
  {
    lines_low(SCL_MASK);
    lines_high(SCL_MASK);
  }
  i2c_stop();
#endif
#ifdef M4_I2C_REGISTER_TEST
  uint8_t diagnostic_id = 0U;
  m4_diagnostic_status[2] = read_who_am_i(0x68U, &diagnostic_id);
  m4_diagnostic_status[1] = diagnostic_id;
  m4_diagnostic_status[0] = 2U;
#endif
#ifdef M4_I2C_SAMPLE_TEST
  (void)write_register(0x68U, 0x6BU, 0x00U);
  (void)write_register(0x68U, 0x1AU, 0x03U);
  (void)write_register(0x68U, 0x19U, 0x13U);
  (void)write_register(0x68U, 0x1BU, 0x00U);
  (void)write_register(0x68U, 0x1CU, 0x00U);
  uint8_t diagnostic_raw[14];
  for (uint32_t sample = 0U; sample < 100U; ++sample)
  {
    const uint32_t sample_start = DWT->CYCCNT;
    m4_diagnostic_status[4] += read_registers(0x68U, 0x3BU, diagnostic_raw, sizeof(diagnostic_raw)) != 0U;
    m4_diagnostic_status[3] = sample + 1U;
    while ((uint32_t)(DWT->CYCCNT - sample_start) < M4_CORE_CLOCK_HZ / 50U)
    {
      OPENAMP_check_for_message();
    }
  }
#endif
  /* Restore only our two pins after two seconds, even if Linux loses SSH. */
  const uint32_t rollback_start = DWT->CYCCNT;
  while ((uint32_t)(DWT->CYCCNT - rollback_start) < 2U * M4_CORE_CLOCK_HZ)
  {
    OPENAMP_check_for_message();
  }
  GPIOB->BSRR = (saved_output & (SDA_MASK | SCL_MASK)) |
               ((~saved_output & (SDA_MASK | SCL_MASK)) << 16U);
  GPIOB->OTYPER = (GPIOB->OTYPER & ~(SDA_MASK | SCL_MASK)) |
                  (saved_type & (SDA_MASK | SCL_MASK));
  GPIOB->OSPEEDR = (GPIOB->OSPEEDR & ~mode_mask) | (saved_speed & mode_mask);
  GPIOB->PUPDR = (GPIOB->PUPDR & ~mode_mask) | (saved_pull & mode_mask);
  GPIOB->MODER = (GPIOB->MODER & ~mode_mask) | (saved_mode & mode_mask);
  m4_diagnostic_status[0] = 3U;
  for (;;)
  {
    OPENAMP_check_for_message();
  }
#endif
  for (uint32_t pulse = 0U; pulse < 9U; ++pulse)
  {
    lines_low(SCL_MASK);
    lines_high(SCL_MASK);
  }
  i2c_stop();
}

static uint32_t fnv1a(const uint8_t *data, uint32_t length)
{
  uint32_t hash = 2166136261UL;
  for (uint32_t index = 0U; index < length; ++index)
  {
    hash ^= data[index];
    hash *= 16777619UL;
  }
  return hash;
}

static void VIRT_UART0_RxCpltCallback(VIRT_UART_HandleTypeDef *huart)
{
  const uint8_t *command = huart->pRxBuffPtr;
  const uint16_t length = huart->RxXferSize;
  if (length == 5U && memcmp(command, "start", 5U) == 0)
  {
    stream_enabled = 1U;
  }
  else if (length == 4U && memcmp(command, "stop", 4U) == 0)
  {
    stream_enabled = 0U;
  }
}

static void MX_IPCC_Init(void)
{
  hipcc.Instance = IPCC;
  if (HAL_IPCC_Init(&hipcc) != HAL_OK)
  {
    Error_Handler();
  }
}

void Error_Handler(void)
{
  __disable_irq();
  for (;;)
  {
  }
}

int main(void)
{
  uint8_t address = 0x68U;
  uint8_t who_am_i = 0U;
  uint8_t raw[14];
  uint16_t i2c_errors = 0U;
  uint16_t tx_errors = 0U;
  uint32_t sequence = 0U;

  HAL_Init();
  CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
  DWT->CYCCNT = 0U;
  DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
  __HAL_RCC_HSEM_CLK_ENABLE();
  MX_IPCC_Init();
  MX_OPENAMP_Init(RPMSG_REMOTE, NULL);
  if (VIRT_UART_Init(&huart0) != VIRT_UART_OK)
  {
    Error_Handler();
  }
  if (VIRT_UART_RegisterCallback(&huart0, VIRT_UART_RXCPLT_CB_ID,
                                 VIRT_UART0_RxCpltCallback) != VIRT_UART_OK)
  {
    Error_Handler();
  }
  /* OpenSTLinux 5.4 uses 0x400 for the first rpmsg_tty endpoint. */
  huart0.ept.dest_addr = 0x400U;

  gpio_i2c_init();
  uint32_t error = read_who_am_i(address, &who_am_i);
  if (error != 0U)
  {
    address = 0x69U;
    error = read_who_am_i(address, &who_am_i);
  }
  if (error == 0U && (who_am_i == 0x68U || who_am_i == 0x69U))
  {
    error |= sensor_configure(address);
  }
  if (error != 0U)
  {
    i2c_errors++;
  }

  const uint32_t sample_cycles = M4_CORE_CLOCK_HZ / (1000U / SAMPLE_PERIOD_MS);
  uint32_t next_sample_cycle = DWT->CYCCNT + sample_cycles;
  uint32_t timestamp_ms = 0U;
  uint32_t zero_streak = 0U;
  uint32_t reinit_cooldown = 0U;
  for (;;)
  {
    OPENAMP_check_for_message();
    const uint32_t now_cycle = DWT->CYCCNT;
    if (stream_enabled == 0U)
    {
      /* Do not accumulate missed deadlines while the Linux reader is closed. */
      next_sample_cycle = now_cycle + sample_cycles;
      continue;
    }
#if M4_MAIN_SAMPLE_LIMIT > 0
    if (sequence >= M4_MAIN_SAMPLE_LIMIT)
    {
      m4_diagnostic_status[0] = 3U;
      continue;
    }
#endif
    if ((int32_t)(now_cycle - next_sample_cycle) < 0)
    {
      continue;
    }
    next_sample_cycle += sample_cycles;
    timestamp_ms += SAMPLE_PERIOD_MS;

    sample_packet_t packet = {0};
    packet.magic = PACKET_MAGIC;
    packet.version = 1U;
    packet.size = (uint16_t)sizeof(packet);
    packet.sequence = sequence++;
    packet.timestamp_ms = timestamp_ms;
    error = read_registers(address, 0x3BU, raw, sizeof(raw));
    if (error == 0U)
    {
      packet.accel_x = signed_word(&raw[0]);
      packet.accel_y = signed_word(&raw[2]);
      packet.accel_z = signed_word(&raw[4]);
      packet.temperature = signed_word(&raw[6]);
      packet.gyro_x = signed_word(&raw[8]);
      packet.gyro_y = signed_word(&raw[10]);
      packet.gyro_z = signed_word(&raw[12]);
    }
    else
    {
      i2c_errors++;
    }
    uint32_t sample_is_zero = 1U;
    for (uint32_t index = 0U; index < sizeof(raw); ++index)
    {
      if (raw[index] != 0U)
      {
        sample_is_zero = 0U;
        break;
      }
    }
    if (error == 0U && sample_is_zero != 0U)
    {
      zero_streak++;
    }
    else
    {
      zero_streak = 0U;
    }
    if (reinit_cooldown != 0U)
    {
      reinit_cooldown--;
    }
    else if (zero_streak >= SENSOR_REINIT_ZERO_FRAMES)
    {
      (void)sensor_configure(address);
      zero_streak = 0U;
      reinit_cooldown = SENSOR_REINIT_COOLDOWN_FRAMES;
    }
    packet.i2c_errors = i2c_errors;
#if M4_MAIN_SAMPLE_LIMIT > 0
    m4_diagnostic_status[1] = who_am_i;
    m4_diagnostic_status[3] = sequence;
    m4_diagnostic_status[4] = i2c_errors;
#endif
    packet.tx_errors = tx_errors;
    packet.checksum = fnv1a((const uint8_t *)&packet,
                            (uint32_t)sizeof(packet) - sizeof(packet.checksum));
#ifndef M4_RPMSG_DISABLE_TX
    if (VIRT_UART_Transmit(&huart0, &packet, sizeof(packet)) != VIRT_UART_OK)
    {
      tx_errors++;
    }
#endif
#ifdef M4_SENSOR_RESET_TEST
    if (sequence == 100U)
    {
      /* 诊断：软复位传感器（模拟掉电重插），用于验证自动重新初始化。 */
      (void)write_register(address, 0x6BU, 0x80U);
    }
#endif
  }
}
