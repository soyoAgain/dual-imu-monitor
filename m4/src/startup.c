#include <stdint.h>

extern uint32_t _estack;
extern uint32_t _sidata;
extern uint32_t _sdata;
extern uint32_t _edata;
extern uint32_t _sbss;
extern uint32_t _ebss;

int main(void);

void Default_Handler(void)
{
  for (;;)
  {
  }
}

void SysTick_Handler(void) __attribute__((weak, alias("Default_Handler")));

void Reset_Handler(void)
{
  uint32_t *source = &_sidata;

  for (uint32_t *destination = &_sdata; destination < &_edata;)
  {
    *destination++ = *source++;
  }

  for (uint32_t *destination = &_sbss; destination < &_ebss;)
  {
    *destination++ = 0U;
  }

  (void)main();
  Default_Handler();
}

typedef void (*vector_handler_t)(void);

__attribute__((section(".isr_vector"), used))
const vector_handler_t vector_table[166] = {
  (vector_handler_t)&_estack,
  Reset_Handler,
  [2 ... 14] = Default_Handler,
  [15] = SysTick_Handler,
  [16 ... 165] = Default_Handler,
};
