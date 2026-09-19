cmake_minimum_required(VERSION 3.20)

project(m4_hello C ASM)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)

set(CUBE_ROOT "${CMAKE_CURRENT_LIST_DIR}/../third_party/STM32CubeMP1")
set(CMSIS_DEVICE "${CUBE_ROOT}/Drivers/CMSIS/Device/ST/STM32MP1xx")

add_executable(m4_hello.elf
  src/startup.c
  src/hello.c
)

target_include_directories(m4_hello.elf PRIVATE
  "${CUBE_ROOT}/Drivers/CMSIS/Core/Include"
  "${CMSIS_DEVICE}/Include"
)

target_compile_definitions(m4_hello.elf PRIVATE
  CORE_CM4
  STM32MP157Dxx
)

target_compile_options(m4_hello.elf PRIVATE
  -mcpu=cortex-m4
  -mthumb
  -mfpu=fpv4-sp-d16
  -mfloat-abi=hard
  -ffunction-sections
  -fdata-sections
  -Wall
  -Wextra
  -Werror
  -Os
)

target_link_options(m4_hello.elf PRIVATE
  -mcpu=cortex-m4
  -mthumb
  -mfpu=fpv4-sp-d16
  -mfloat-abi=hard
  -nostdlib
  -Wl,--gc-sections
  -Wl,-Map=${CMAKE_CURRENT_BINARY_DIR}/m4_hello.map
  -T${CMAKE_CURRENT_LIST_DIR}/linker/stm32mp157_m4.ld
)

add_custom_command(TARGET m4_hello.elf POST_BUILD
  COMMAND ${CMAKE_SIZE} $<TARGET_FILE:m4_hello.elf>
  COMMAND ${CMAKE_OBJCOPY} -O binary
          $<TARGET_FILE:m4_hello.elf>
          ${CMAKE_CURRENT_BINARY_DIR}/m4_hello.bin
)

add_executable(m4_mpu6050.elf
  src/startup.c
  src/mpu6050.c
)

target_include_directories(m4_mpu6050.elf PRIVATE
  "${CUBE_ROOT}/Drivers/CMSIS/Core/Include"
  "${CMSIS_DEVICE}/Include"
)

target_compile_definitions(m4_mpu6050.elf PRIVATE
  CORE_CM4
  STM32MP157Dxx
)

target_compile_options(m4_mpu6050.elf PRIVATE
  -mcpu=cortex-m4
  -mthumb
  -mfpu=fpv4-sp-d16
  -mfloat-abi=hard
  -ffunction-sections
  -fdata-sections
  -Wall
  -Wextra
  -Werror
  -Os
)

target_link_options(m4_mpu6050.elf PRIVATE
  -mcpu=cortex-m4
  -mthumb
  -mfpu=fpv4-sp-d16
  -mfloat-abi=hard
  -nostdlib
  -Wl,--gc-sections
  -Wl,-Map=${CMAKE_CURRENT_BINARY_DIR}/m4_mpu6050.map
  -T${CMAKE_CURRENT_LIST_DIR}/linker/stm32mp157_m4.ld
)

add_custom_command(TARGET m4_mpu6050.elf POST_BUILD
  COMMAND ${CMAKE_SIZE} $<TARGET_FILE:m4_mpu6050.elf>
  COMMAND ${CMAKE_OBJCOPY} -O binary
          $<TARGET_FILE:m4_mpu6050.elf>
          ${CMAKE_CURRENT_BINARY_DIR}/m4_mpu6050.bin
)

set(OPENAMP_APP
  "${CUBE_ROOT}/Projects/STM32MP157C-DK2/Applications/OpenAMP/OpenAMP_TTY_echo")
set(OPENAMP_MW "${CUBE_ROOT}/Middlewares/Third_Party/OpenAMP")

option(M4_OPENAMP_PROBE_ONLY "Build OpenAMP without MPU6050 or GPIO access" OFF)
option(M4_RPMSG_DISABLE_TX "Diagnostic: sample MPU6050 without transmitting RPMsg packets" OFF)
option(M4_GPIO_CONFIG_ONLY "Diagnostic: configure GPIO only, without I2C pulses or sampling" OFF)
option(M4_I2C_RECOVERY_ONLY "Diagnostic: add I2C bus recovery before GPIO rollback" OFF)
option(M4_I2C_REGISTER_TEST "Diagnostic: read WHO_AM_I before GPIO rollback" OFF)
option(M4_I2C_SAMPLE_TEST "Diagnostic: configure sensor and sample for two seconds before GPIO rollback" OFF)
option(M4_SENSOR_RESET_TEST "Diagnostic: soft-reset the sensor once to verify automatic re-initialization" OFF)
set(M4_MAIN_SAMPLE_LIMIT 0 CACHE STRING "Diagnostic: stop the original sampling loop after N samples; 0 is unlimited")
if(M4_OPENAMP_PROBE_ONLY)
  set(M4_RPMSG_MAIN src/openamp_probe.c)
else()
  set(M4_RPMSG_MAIN src/mpu6050_rpmsg.c)
endif()

add_executable(m4_rpmsg.elf
  ${M4_RPMSG_MAIN}
  "${OPENAMP_APP}/Src/system_stm32mp1xx.c"
  "${OPENAMP_APP}/Src/stm32mp1xx_hal_msp.c"
  "${OPENAMP_APP}/Src/stm32mp1xx_it.c"
  "${OPENAMP_APP}/Src/lock_resource.c"
  "${OPENAMP_APP}/Src/mbox_ipcc.c"
  "${OPENAMP_APP}/Src/openamp.c"
  "${OPENAMP_APP}/Src/rsc_table.c"
  "${OPENAMP_APP}/STM32CubeIDE/CM4/Application/Startup/startup_stm32mp157caax.s"
  "${CUBE_ROOT}/Drivers/BSP/STM32MP15xx_DISCO/stm32mp15xx_disco.c"
  "${CUBE_ROOT}/Drivers/STM32MP1xx_HAL_Driver/Src/stm32mp1xx_hal.c"
  "${CUBE_ROOT}/Drivers/STM32MP1xx_HAL_Driver/Src/stm32mp1xx_hal_cortex.c"
  "${CUBE_ROOT}/Drivers/STM32MP1xx_HAL_Driver/Src/stm32mp1xx_hal_dma.c"
  "${CUBE_ROOT}/Drivers/STM32MP1xx_HAL_Driver/Src/stm32mp1xx_hal_gpio.c"
  "${CUBE_ROOT}/Drivers/STM32MP1xx_HAL_Driver/Src/stm32mp1xx_hal_hsem.c"
  "${CUBE_ROOT}/Drivers/STM32MP1xx_HAL_Driver/Src/stm32mp1xx_hal_ipcc.c"
  "${CUBE_ROOT}/Drivers/STM32MP1xx_HAL_Driver/Src/stm32mp1xx_hal_mdma.c"
  "${CUBE_ROOT}/Drivers/STM32MP1xx_HAL_Driver/Src/stm32mp1xx_hal_pwr.c"
  "${CUBE_ROOT}/Drivers/STM32MP1xx_HAL_Driver/Src/stm32mp1xx_hal_pwr_ex.c"
  "${CUBE_ROOT}/Drivers/STM32MP1xx_HAL_Driver/Src/stm32mp1xx_hal_rcc.c"
  "${CUBE_ROOT}/Drivers/STM32MP1xx_HAL_Driver/Src/stm32mp1xx_hal_rcc_ex.c"
  src/virt_uart_compat.c
  "${OPENAMP_MW}/libmetal/lib/device.c"
  "${OPENAMP_MW}/libmetal/lib/init.c"
  "${OPENAMP_MW}/libmetal/lib/io.c"
  "${OPENAMP_MW}/libmetal/lib/log.c"
  "${OPENAMP_MW}/libmetal/lib/system/generic/generic_device.c"
  "${OPENAMP_MW}/libmetal/lib/system/generic/generic_init.c"
  "${OPENAMP_MW}/libmetal/lib/system/generic/generic_io.c"
  "${OPENAMP_MW}/libmetal/lib/system/generic/template/sys.c"
  "${OPENAMP_MW}/open-amp/lib/remoteproc/remoteproc_virtio.c"
  "${OPENAMP_MW}/open-amp/lib/rpmsg/rpmsg.c"
  "${OPENAMP_MW}/open-amp/lib/rpmsg/rpmsg_virtio.c"
  "${OPENAMP_MW}/open-amp/lib/virtio/virtio.c"
  "${OPENAMP_MW}/open-amp/lib/virtio/virtqueue.c"
)

target_include_directories(m4_rpmsg.elf PRIVATE
  "${OPENAMP_APP}/Inc"
  "${CUBE_ROOT}/Drivers/STM32MP1xx_HAL_Driver/Inc"
  "${CUBE_ROOT}/Drivers/STM32MP1xx_HAL_Driver/Inc/Legacy"
  "${CUBE_ROOT}/Drivers/CMSIS/Include"
  "${CMSIS_DEVICE}/Include"
  "${CUBE_ROOT}/Drivers/BSP/STM32MP15xx_DISCO"
  "${OPENAMP_MW}/virtual_driver"
  "${OPENAMP_MW}/libmetal/lib/include"
  "${OPENAMP_MW}/open-amp/lib/include"
)

target_compile_definitions(m4_rpmsg.elf PRIVATE
  M4_MAIN_SAMPLE_LIMIT=${M4_MAIN_SAMPLE_LIMIT}
  CORE_CM4
  STM32MP157Dxx
  USE_HAL_DRIVER
  METAL_MAX_DEVICE_REGIONS=2
  NO_ATOMIC_64_SUPPORT
  METAL_INTERNAL
  VIRTIO_SLAVE_ONLY
)
if(M4_RPMSG_DISABLE_TX)
  target_compile_definitions(m4_rpmsg.elf PRIVATE M4_RPMSG_DISABLE_TX=1)
endif()
if(M4_GPIO_CONFIG_ONLY)
  target_compile_definitions(m4_rpmsg.elf PRIVATE M4_GPIO_CONFIG_ONLY=1)
endif()
if(M4_I2C_RECOVERY_ONLY)
  target_compile_definitions(m4_rpmsg.elf PRIVATE M4_GPIO_CONFIG_ONLY=1 M4_I2C_RECOVERY_ONLY=1)
endif()
if(M4_I2C_REGISTER_TEST)
  target_compile_definitions(m4_rpmsg.elf PRIVATE M4_GPIO_CONFIG_ONLY=1 M4_I2C_RECOVERY_ONLY=1 M4_I2C_REGISTER_TEST=1)
endif()
if(M4_I2C_SAMPLE_TEST)
  target_compile_definitions(m4_rpmsg.elf PRIVATE M4_GPIO_CONFIG_ONLY=1 M4_I2C_RECOVERY_ONLY=1 M4_I2C_REGISTER_TEST=1 M4_I2C_SAMPLE_TEST=1)
endif()
if(M4_SENSOR_RESET_TEST)
  target_compile_definitions(m4_rpmsg.elf PRIVATE M4_SENSOR_RESET_TEST=1)
endif()

target_compile_options(m4_rpmsg.elf PRIVATE
  -mcpu=cortex-m4 -mthumb -mfpu=fpv4-sp-d16 -mfloat-abi=hard
  -ffunction-sections -fdata-sections -Wall -Wextra -Os
)

target_link_options(m4_rpmsg.elf PRIVATE
  -mcpu=cortex-m4 -mthumb -mfpu=fpv4-sp-d16 -mfloat-abi=hard
  --specs=nano.specs --specs=nosys.specs
  -Wl,--gc-sections
  -Wl,-Map=${CMAKE_CURRENT_BINARY_DIR}/m4_rpmsg.map
  -T${OPENAMP_APP}/STM32CubeIDE/CM4/STM32MP157CAAX_RAM.ld
)

add_custom_command(TARGET m4_rpmsg.elf POST_BUILD
  COMMAND ${CMAKE_SIZE} $<TARGET_FILE:m4_rpmsg.elf>
)
