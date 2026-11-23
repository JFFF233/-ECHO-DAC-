.syntax unified
.cpu cortex-m3
.thumb

.equ STATE,          0x03024c78
.equ USB_CLASS,      0x03024c1c
.equ OUTPUT_ENABLE,  0x0302235e
.equ PCM_WIDTH,      0x0302dd84
.equ HOST_STATE,     0x0302f588 + 60
.equ HOST_DB,        0
.equ HOST_MUTE,      2
.equ HOST_LOSS,      4
.equ MAX_LOSS_DB,    96
.equ UNITY_GAIN,     1 << 30

// The two valid SET payload lengths also identify the control selector.
host_set_complete:
    push {r4, lr}
    bl   0x0307f236
    ldrh r1, [r0]
    ldrh r2, [r0, #2]
    cmp  r1, r2
    bne  set_done
    ldr  r0, [r0, #4]
    ldr  r4, =HOST_STATE
    cmp  r1, #1
    bne  set_volume
    ldrb r0, [r0]
    cmp  r0, #1
    it   ls
    strbls r0, [r4, #HOST_MUTE]
    b    set_done
set_volume:
    ldrsh r0, [r0]
    mov.w r3, #UNITY_GAIN
    cmn.w r0, #0x8000            // The USB Audio silence value.
    beq  volume_silence
    cmp  r0, #0
    it   gt
    movgt r0, #0
    rsbs r1, r0, #0
    adds r1, #128
    lsrs r1, #8
    cmp  r1, #MAX_LOSS_DB
    it   hi
    movhi r1, #MAX_LOSS_DB
    rsbs r0, r1, #0
    lsls r0, #8
    cbz  r1, store_volume
    // Apply the linear gain for -1 dB once per step, outside the DMA loop.
    ldr  r2, =0xe4290580          // round(2^32 * 10^(-1/20))
volume_step:
    umull ip, r3, r3, r2
    subs r1, #1
    bne  volume_step
    b    store_volume
volume_silence:
    movs r3, #0
store_volume:
    strh r0, [r4, #HOST_DB]
    sub.w r3, r3, #UNITY_GAIN    // Store zero for unity after BSS reset.
    str  r3, [r4, #HOST_LOSS]
set_done:
    pop  {r4, pc}

send_control:
    ldrh r0, [r4, #6]
    cmp  r0, r3
    it   hi
    movhi r0, r3
    bl   0x0307eb72
class_done:
    movs r0, #0
    pop.w {r3, r4, r5, r6, r7, r8, r9, pc}
.ltorg

.org 0x0307fa48

.org 0x0307fb04
// The stock class dispatcher has already saved r3-r8, r9 and lr, with the
// setup packet in r4 and bmRequestType in r2.
host_class_setup:
    cmp  r2, #0x22
    it   ne
    cmpne r2, #0xa2
    beq  ack_class
    cmp  r2, #0x21
    it   ne
    cmpne r2, #0xa1
    bne  stall_class
    ldrh r0, [r4, #4]
    ldrb r5, [r4, #1]
    cmp.w r0, #0x0300
    bne  other_interface
    ldrh r1, [r4, #2]
    cmp.w r1, #0x0100             // Master mute, one byte.
    beq  control_width
    cmp.w r1, #0x0200             // Master volume, two bytes.
    bne  stall_class
control_width:
    lsrs r3, r1, #8
    cmp  r2, #0x21
    bne  read_control
    cmp  r5, #1                   // SET_CUR on both speeds.
    bne  stall_class
    ldrh r5, [r4, #6]
    cmp  r5, r3
    bne  stall_class
    bl   0x0307f236
    strh r5, [r0]
    movs r1, #0
    strb r1, [r0, #12]
    ldr  r1, =(__SET_COMPLETE__ | 1)
    str  r1, [r0, #8]
    bl   0x0307ee00
    b    class_done

read_control:
    ldr  r0, =USB_CLASS
    ldrb r0, [r0]
    cmp  r0, #3
    beq  read_uac2
    cmp  r5, #0x81                // UAC1 GET_CUR.
    beq  read_current
    cmp  r3, #2
    bne  stall_class
    subs r5, #0x82                // UAC1 GET_MIN, GET_MAX, GET_RES.
    cmp  r5, #2
    bhi  stall_class
    adr  r1, volume_range_limits
    add.w r1, r1, r5, lsl #1
    b    send_control
read_uac2:
    cmp  r5, #1
    beq  read_current
    cmp  r5, #2                   // UAC2 GET_RANGE for Volume only.
    it   eq
    cmpeq r3, #2
    bne  stall_class
    adr  r1, volume_range
    movs r3, #8
    b    send_control
read_current:
    ldr  r1, =HOST_STATE + HOST_DB
    cmp  r3, #1
    it   eq
    addeq r1, #(HOST_MUTE - HOST_DB)
    b    send_control
other_interface:
    cmp  r0, #2                   // Preserve the HID interface's idle request.
    it   eq
    cmpeq r5, #0x0a
    bne  stall_class
ack_class:
    bl   0x0307eeae
    b    class_done
stall_class:
    lsrs r0, r2, #7               // Stall the requested data direction.
    ldrh r1, [r4, #6]
    cbnz r1, stall_ep0
    movs r0, #1                  // No data stage has an IN status stage.
stall_ep0:
    bl   0x0307ee3c
    b    class_done
.balign 4
volume_range:
    .short 1
volume_range_limits:
    .short (-MAX_LOSS_DB * 256), 0, 256
.ltorg
.org 0x0307fbbe

.org 0x0307fd7c
// Read the host control once per DMA block. The device keys still drive
// the DAC's analogue volume, including the local output enable flag.
scale_host_volume:
    push {r4, r5, r6, r7, lr}
    ldr  r2, [r1]
    movs r5, #0
    ldr  r4, =STATE
    ldr  r1, =HOST_STATE
    ldrb r3, [r1, #HOST_MUTE]
    ldr  r6, [r1, #HOST_LOSS]
    ldr  r1, =OUTPUT_ENABLE
    ldrb r1, [r1]
    cbz  r1, mute_samples
    cbz  r3, sample_width
mute_samples:
    mov.w r6, #-UNITY_GAIN
sample_width:
    add.w r3, r6, #UNITY_GAIN
    ldr  r1, =PCM_WIDTH
    ldrb r1, [r1]
    and  r1, r1, #16              // Bit 4 is set for 16 and 24 bits.
    lsrs r1, #1
    mov  lr, r1
    mov  r7, r2
    cbz  r7, count_silence
scale_sample:
    ldr  r1, [r0]
    asr.w r1, r1, lr
    cmp  r6, #0
    beq  store_sample
    smull ip, r1, r1, r3
    lsls r1, #2
    orr.w r1, r1, ip, lsr #30
    bpl  store_sample
    // Truncate a negative fractional product toward zero.
    lsls.w ip, ip, #2
    it   ne
    addne r1, #1
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
    lsrs r0, r2, #1
    it   ne
    movne r0, #1
    str  r0, [r4, #0x38]
    pop  {r4, r5, r6, r7, pc}
.ltorg
.org 0x0307fdfa
