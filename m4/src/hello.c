#include <stdint.h>
#include "stm32mp1xx.h"

/* 正点原子系统设备树实测：user-led = GPIOF pin 3，低电平点亮。 */
#define USER_LED_PIN 3U
#define USER_LED_MASK (1UL << USER_LED_PIN)

struct remoteproc_resource_table
{
  uint32_t version;
  uint32_t entries;
  uint32_t reserved[2];
};

__attribute__((section(".resource_table"), used))
const struct remoteproc_resource_table resource_table = {
  .version = 1U,
  .entries = 0U,
  .reserved = {0U, 0U},
};

static void delay_visible(void)
{
  /* 生产模式下 M4 时钟由 A7/Linux 配置；这里只要求肉眼可见，不作为精确定时。 */
  for (volatile uint32_t count = 0U; count < 18000000U; ++count)
  {
    __asm volatile ("nop");
  }
}

static uint32_t gpio_read_stable(volatile uint32_t *reg)
{
  /* 使能 GPIO 时钟后总线需要若干周期才可访问。若立即读-改-写，读回 0
     会把其他引脚的 MODER/OSPEEDR/PUPDR 清零。 */
  uint32_t value = *reg;
  for (uint32_t attempt = 0U; attempt < 100000U && value == 0U; ++attempt)
  {
    value = *reg;
  }
  return value;
}

static void user_led_init(void)
{
  RCC->MC_AHB4ENSETR = RCC_MC_AHB4ENSETR_GPIOFEN;
  (void)RCC->MC_AHB4ENSETR;

  const uint32_t moder = gpio_read_stable(&GPIOF->MODER);

  GPIOF->MODER = (moder & ~(3UL << (USER_LED_PIN * 2U))) |
                 (1UL << (USER_LED_PIN * 2U));
  GPIOF->OTYPER &= ~USER_LED_MASK;
  GPIOF->OSPEEDR &= ~(3UL << (USER_LED_PIN * 2U));
  GPIOF->PUPDR &= ~(3UL << (USER_LED_PIN * 2U));

  /* BSRR 低 16 位写 1 置位：PF3=1，LED 熄灭。 */
  GPIOF->BSRR = USER_LED_MASK;
}

int main(void)
{
  user_led_init();

  for (;;)
  {
    /* BSRR 高 16 位写 1 复位：PF3=0，LED 点亮。 */
    GPIOF->BSRR = USER_LED_MASK << 16U;
    delay_visible();

    GPIOF->BSRR = USER_LED_MASK;
    delay_visible();
  }
}
