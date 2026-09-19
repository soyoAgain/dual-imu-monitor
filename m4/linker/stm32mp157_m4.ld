ENTRY(Reset_Handler)

_estack = 0x10040000;

MEMORY
{
  VECTORS (RX) : ORIGIN = 0x00000000, LENGTH = 0x00000298
  TEXT    (RX) : ORIGIN = 0x10000000, LENGTH = 0x00020000
  DATA    (RW) : ORIGIN = 0x10020000, LENGTH = 0x00020000
}

SECTIONS
{
  .isr_vector :
  {
    KEEP(*(.isr_vector))
  } > VECTORS

  .text :
  {
    . = ALIGN(4);
    *(.text*)
    *(.rodata*)
    . = ALIGN(4);
    _etext = .;
  } > TEXT

  .data : AT(_etext)
  {
    . = ALIGN(4);
    _sdata = .;
    *(.data*)
    . = ALIGN(4);
    _edata = .;
  } > DATA
  _sidata = LOADADDR(.data);

  .resource_table :
  {
    . = ALIGN(4);
    KEEP(*(.resource_table*))
    . = ALIGN(4);
  } > DATA AT > TEXT

  .shared_status 0x10020100 (NOLOAD) :
  {
    . = ALIGN(4);
    KEEP(*(.shared_status*))
    . = ALIGN(4);
  } > DATA

  .bss (NOLOAD) :
  {
    . = ALIGN(4);
    _sbss = .;
    *(.bss*)
    *(COMMON)
    . = ALIGN(4);
    _ebss = .;
  } > DATA

  /DISCARD/ :
  {
    *(.comment*)
    *(.ARM.exidx*)
    *(.ARM.extab*)
  }

  .ARM.attributes 0 : { *(.ARM.attributes) }
}

ASSERT(SIZEOF(.isr_vector) <= LENGTH(VECTORS), "vector table is too large")
ASSERT((_etext - ORIGIN(TEXT)) <= LENGTH(TEXT), "M4 text region overflow")
ASSERT((_ebss - ORIGIN(DATA)) <= LENGTH(DATA), "M4 data region overflow")
