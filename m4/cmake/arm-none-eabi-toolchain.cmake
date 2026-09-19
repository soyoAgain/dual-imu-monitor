set(CMAKE_SYSTEM_NAME Generic)
set(CMAKE_SYSTEM_PROCESSOR arm)
set(CMAKE_TRY_COMPILE_TARGET_TYPE STATIC_LIBRARY)

set(ARM_TOOLCHAIN_BIN
  "${CMAKE_CURRENT_LIST_DIR}/../../third_party/arm-toolchain-extracted-15.3/Payload/bin")

set(CMAKE_C_COMPILER "${ARM_TOOLCHAIN_BIN}/arm-none-eabi-gcc")
set(CMAKE_ASM_COMPILER "${ARM_TOOLCHAIN_BIN}/arm-none-eabi-gcc")
set(CMAKE_OBJCOPY "${ARM_TOOLCHAIN_BIN}/arm-none-eabi-objcopy")
set(CMAKE_SIZE "${ARM_TOOLCHAIN_BIN}/arm-none-eabi-size")
