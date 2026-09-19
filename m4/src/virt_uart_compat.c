#include "virt_uart.h"
#include "metal/utilities.h"

/* OpenSTLinux 5.4 rpmsg_tty matches this legacy service name. */
#define RPMSG_SERVICE_NAME "rpmsg-tty-channel"

static int VIRT_UART_read_cb(struct rpmsg_endpoint *ept, void *data,
                             size_t len, uint32_t src, void *priv)
{
  VIRT_UART_HandleTypeDef *huart =
      metal_container_of(ept, VIRT_UART_HandleTypeDef, ept);
  (void)src;
  (void)priv;
  huart->pRxBuffPtr = data;
  huart->RxXferSize = len;
  if (huart->RxCpltCallback != NULL)
  {
    huart->RxCpltCallback(huart);
  }
  return 0;
}

VIRT_UART_StatusTypeDef VIRT_UART_Init(VIRT_UART_HandleTypeDef *huart)
{
  return OPENAMP_create_endpoint(&huart->ept, RPMSG_SERVICE_NAME,
                                 RPMSG_ADDR_ANY, VIRT_UART_read_cb, NULL) < 0
             ? VIRT_UART_ERROR
             : VIRT_UART_OK;
}

VIRT_UART_StatusTypeDef VIRT_UART_DeInit(VIRT_UART_HandleTypeDef *huart)
{
  OPENAMP_destroy_ept(&huart->ept);
  return VIRT_UART_OK;
}

VIRT_UART_StatusTypeDef VIRT_UART_RegisterCallback(
    VIRT_UART_HandleTypeDef *huart, VIRT_UART_CallbackIDTypeDef callback_id,
    void (*callback)(VIRT_UART_HandleTypeDef *))
{
  if (callback_id != VIRT_UART_RXCPLT_CB_ID)
  {
    return VIRT_UART_ERROR;
  }
  huart->RxCpltCallback = callback;
  return VIRT_UART_OK;
}

VIRT_UART_StatusTypeDef VIRT_UART_Transmit(VIRT_UART_HandleTypeDef *huart,
                                           const void *data, uint16_t size)
{
  if (size > (RPMSG_BUFFER_SIZE - 16U) ||
      OPENAMP_send(&huart->ept, data, size) < 0)
  {
    return VIRT_UART_ERROR;
  }
  return VIRT_UART_OK;
}
