.syntax unified
.cpu cortex-m3
.thumb

.equ USB,           0x60080000
.equ STREAM,        0x0302f588
.equ USB_CLASS,     0x03024c1c
.equ TICK,          0x03022330
.equ LAST_PACKET,   48
.equ LAST_TICK,     52
.equ PHASE,         56

// This body occupies the old packet receiver. Its dispatch always enters
// receive_audio in the main extension before reaching this stock body.
watch_usb:
    push {r4, r5, r6, lr}
    ldr  r4, =USB
    ldr  r5, =STREAM
    ldr  r0, =USB_CLASS
    ldrb r0, [r0]
    cmp  r0, #3
    bne  recovery_cancel
    ldr  r0, [r5]
    ldr  r1, [r5, #4]
    cmp  r0, r1
    bne  recovery_cancel
    uxtb r0, r0
    cbz  r0, recovery_cancel
    ldr  r6, =TICK
    ldr  r6, [r6]
    ldr  r0, [r5, #PHASE]
    cmp  r0, #0
    bne.w recovery_phase
    ldr  r0, [r5, #12]
    ldr  r1, [r5, #LAST_PACKET]
    cmp  r0, r1
    bne  recovery_progress
    ldr  r1, [r5, #LAST_TICK]
    subs r1, r6, r1
    cmp  r1, #5
    blo  recovery_done
    ldr  r0, [r4, #0x804]
    bic  r0, r0, #0x600
    orr  r0, r0, #0x200
    str  r0, [r4, #0x804]
    movs r0, #1
    str  r0, [r5, #PHASE]
    str  r6, [r5, #LAST_TICK]
    b    recovery_done
recovery_progress:
    str  r0, [r5, #LAST_PACKET]
    str  r6, [r5, #LAST_TICK]
recovery_done:
    pop  {r4, r5, r6, pc}
recovery_cancel:
    ldr  r0, [r5, #PHASE]
    cmp  r0, #0
    beq  recovery_done
    b    recovery_clear
.ltorg
.org 0x0307FC78

// Leave the live event dispatch and ring/volume code between the bodies.
.org 0x0307FE72
recovery_phase:
    ldr  r1, [r5, #LAST_TICK]
    subs r1, r6, r1
    cmp  r1, #100
    bhs  recovery_clear
    cmp  r0, #1
    bne  wait_endpoint_disabled
    ldr  r0, [r4, #0x14]
    tst  r0, #0x80
    beq  recovery_done
    ldr  r0, [r4, #0x81c]
    bic  r0, r0, #0x40000
    str  r0, [r4, #0x81c]
    ldr  r0, [r4, #0xb40]
    orr  r0, r0, #0x48000000
    str  r0, [r4, #0xb40]
    movs r0, #2
    str  r0, [r5, #PHASE]
    b    recovery_done
wait_endpoint_disabled:
    ldr  r0, [r4, #0xb48]
    tst  r0, #2
    beq  recovery_done
    ldrb r0, [r5]
    bl   0x0308050c
recovery_clear:
    ldr  r0, [r4, #0x804]
    bic  r0, r0, #0x600
    orr  r0, r0, #0x400
    str  r0, [r4, #0x804]
    ldr  r0, [r4, #0x81c]
    orr  r0, r0, #0x40000
    str  r0, [r4, #0x81c]
    movs r0, #0
    str  r0, [r5, #PHASE]
    b    recovery_done
