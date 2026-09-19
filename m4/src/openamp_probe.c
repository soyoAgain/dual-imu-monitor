#include "main.h"
#include "virt_uart.h"

IPCC_HandleTypeDef hipcc;
static VIRT_UART_HandleTypeDef huart0;

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
  HAL_Init();
  __HAL_RCC_HSEM_CLK_ENABLE();
  MX_IPCC_Init();
  MX_OPENAMP_Init(RPMSG_REMOTE, NULL);
  if (VIRT_UART_Init(&huart0) != VIRT_UART_OK)
  {
    Error_Handler();
  }

  /* Linux 5.4 allocates the first rpmsg_tty endpoint at 0x400. */
  huart0.ept.dest_addr = 0x400U;
  for (;;)
  {
    OPENAMP_check_for_message();
  }
}
