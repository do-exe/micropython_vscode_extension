.syntax unified
.cpu cortex-m0
.thumb

.section .isr_vector, "a", %progbits
.word 0x20001000
.word Reset_Handler

.text
.thumb_func
.global Reset_Handler
Reset_Handler:
  bl main
1:
  b 1b
