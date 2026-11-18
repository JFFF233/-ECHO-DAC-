.syntax unified
.cpu cortex-m3
.thumb

.equ STATE,         0x03024c78
.equ UAC_GAIN,      0x03024b60
.equ OUTPUT_ENABLE, 0x0302235e
.equ PCM_WIDTH,     0x0302dd84

// Apply the existing 0..60 gain to each signed DMA word.
// Divide first and scale the quotient and remainder separately. Neither
// product can overflow, including for INT32_MIN.
scale_volume:
    push {r4, r5, r6, r7, lr}
    ldr  r2, [r1]
    movs r5, #0
    ldr  r1, =UAC_GAIN
    addw r4, r1, #(STATE - UAC_GAIN)
    ldrb r3, [r1]
    cmp  r3, #60
    it   hi
    movhi r3, #60
    ldrb r1, [r1, #1]
    cmp  r1, #1
    beq  mute
    ldr  r1, =OUTPUT_ENABLE
    ldrb r1, [r1]
    cbnz r1, get_sample_width
mute:
    movs r3, #0
get_sample_width:
    ldr  r1, =PCM_WIDTH
    ldrb r1, [r1]
    // Bit 4 is set for 16- and 24-bit modes.
    and  r1, r1, #16
    lsrs r1, #1
    mov  lr, r1
    movs r6, #60
    mov  r7, r2
    cbz  r7, count_silence
scale_sample:
    ldr  r1, [r0]
    asr.w r1, r1, lr
    cmp  r3, #60
    beq  store_sample
    sdiv ip, r1, r6
    mls  r1, ip, r6, r1
    muls r1, r3, r1
    sdiv r1, r1, r6
    mla  r1, ip, r3, r1
store_sample:
    lsl.w r1, r1, lr
    str  r1, [r0], #4
    cbnz r1, next_sample
    adds r5, #1
next_sample:
    subs r7, #1
    bne  scale_sample
count_silence:
    str  r5, [r4, #0x34]
    subs r2, r2, r5
    movs r0, #0
    cmp  r2, #2
    it   hs
    movhs r0, #1
    str  r0, [r4, #0x38]
    pop  {r4, r5, r6, r7, pc}
.ltorg
