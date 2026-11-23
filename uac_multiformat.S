.syntax unified
.cpu cortex-m3
.thumb

.equ STATE,            0x03024c78
.equ CONFIGURED,       0x03024935
.equ STAGING,          0x0302ed88
.equ STREAM,           0x0302f588
.equ REQUESTED,         0
.equ APPLIED,           4
.equ PACKET_SIZE,      0x03024b5c
.equ PCM_INFO,         0x0302dce8
.equ DMA_BUFFER,       __DMA_BUFFER__
.equ DMA_HALF_SHIFT,   __DMA_HALF_SHIFT__
.equ USB,              0x60080000
.equ DCTL,              0x804
.equ DAINTMSK,          0x81c
.equ DIEPCTL0,          0x900
.equ DOEPCTL2,          0xb40
.equ DOEPINT2,          0xb48
.equ DOEPTSIZ2,         0xb50
.equ FIFO2,            0x60083000
.equ HID_EP_IN,        0x03024be8
.equ MODES,            __MODE_TABLE__
.equ MAX_ALTERNATE,    __MAX_ALTERNATE__
.equ DIAGNOSTICS,      __DIAGNOSTICS__
.equ CLOCK_FIX,        __CLOCK_FIX__
.equ I2S1_CKR,         0x40030008
.equ HIGH_SPEED,       __HIGH_SPEED__
.equ DMA_PIPELINE,     __DMA_PIPELINE__
.equ REVISED,          __REVISED__
.equ MODE_SHIFT,       __MODE_SHIFT__
.equ RECOVERY,         __RECOVERY__
.equ RATE_192,         __RATE_192__
.equ PACKED_192,       __PACKED_192__
.equ PCM_32,           __PCM_32__
.equ HOST_VOLUME,      __HOST_VOLUME__
.if REVISED
.equ RING,            0x03024ce8
.equ RX_DROPPED,       32
.equ DMA_SHORT,        36
.equ DMA_MASK,         40
.equ RING_LEVEL,       44
.equ DETAIL_STRING,   STREAM + 128
.endif

.if HIGH_SPEED
.equ USB_CLASS,       0x03024c1c
.equ DSTS,             0x808
.equ HS_MODES,         __HS_MODE_TABLE__
.equ RATE_OFFSET,      28
.if RATE_192
.equ HOST_ALTERNATE,   29
.endif
.endif

.if DIAGNOSTICS
.equ DIAG_STRING,      0x03024900
.equ DIAG_ENABLED,     8
.equ DIAG_ALTERNATE,   9
.equ DIAG_CLOCKED,     10
.equ DIAG_RX,          12
.equ DIAG_MAX,         16
.equ DIAG_READY,       20
.equ DIAG_DMA,         24
.if PCM_32
.equ DIAG_MODE_BITS,   4
.equ DIAG_CLOCK_SHIFT, 8
.else
.equ DIAG_MODE_BITS,   3
.equ DIAG_CLOCK_SHIFT, 9
.endif
.endif

// Fixed four-byte entry points. The image builder binds these to module 11.
b.w setup_request
b.w receive_event
b.w reset_event
b.w disconnect_event
b.w poll_audio
b.w monitor_audio
b.w initialise_pcm
b.w elapsed_ms
b.w dma_start_gate
b.w fill_dma
.if CLOCK_FIX
b.w configure_i2s
.endif
.if DMA_PIPELINE
b.w dma_complete
b.w prime_recovery
.endif
.if REVISED
b.w read_ring_words
b.w out_token_disabled
.endif
.if RECOVERY
.if RATE_192
b.w arm_current_endpoint
.else
b.w arm_endpoint
.endif
.endif

// Shape: a generation in bits 31:8 and the selected alternate in bits 7:0.
// USB callbacks publish a generation. The audio task applies it after DMA
// has stopped. While these words differ incoming samples are discarded.
publish_alternate:
    ldr  r1, =STREAM
    ldr  r2, [r1, #REQUESTED]
    uxtb r3, r2
    cmp  r0, r3
    beq  publish_done
    add.w r2, r2, #256
    bic  r2, r2, #255
    orrs r2, r0
    ldr  r3, =STATE
    movs r0, #4
    strh r0, [r3, #6]
    str  r2, [r1, #REQUESTED]
publish_done:
    bx   lr

setup_request:
    ldrb r2, [r0]
    ldrb r3, [r0, #1]
    cmp  r2, #0x01
    bne  check_get_interface
    cmp  r3, #11
    beq  set_interface
check_get_interface:
    cmp  r2, #0x81
    bne  check_configuration
    cmp  r3, #10
    beq  get_interface
check_configuration:
    cmp  r2, #0x00
    bne  stock_setup
    cmp  r3, #9
    beq  set_configuration
stock_setup:
.if HIGH_SPEED
    cmp  r2, #0x80
    it   eq
    cmpeq r3, #6
    beq.w dual_speed_descriptor
    cmp  r2, #0xa1
    it   ne
    cmpne r2, #0x21
    bne  stock_setup_request
    ldr  ip, =USB_CLASS
    ldrb ip, [ip]
    cmp  ip, #3
    beq.w uac2_control
stock_setup_request:
.endif
    b.w  0x0307fa48

set_interface:
    ldrh r2, [r0, #6]
    cmp  r2, #0
    bne.w reject_setup
    ldrh r2, [r0, #4]
    ldrh r0, [r0, #2]
    cmp  r2, #1
    beq  change_streaming
    cmp  r2, #2
    bhi  reject_setup
    cmp  r0, #0
    bne.w reject_setup
    b    acknowledge_setup
change_streaming:
.if HIGH_SPEED
    ldr  r1, =USB_CLASS
    ldrb r1, [r1]
    cmp  r1, #3
    bne  full_speed_alternate
.if RATE_192
.if PCM_32
    cmp  r0, #5
.else
    cmp  r0, #4
.endif
    bhi  reject_setup
.else
    cmp  r0, #2
    bhi  reject_setup
    cbz  r0, check_configured
    ldr  r1, =STREAM
    ldrb r1, [r1, #RATE_OFFSET]
    adds r0, r0, r1
.endif
    b    check_configured
full_speed_alternate:
.endif
    cmp  r0, #MAX_ALTERNATE
    bhi  reject_setup
check_configured:
    ldr  r1, =STATE
    ldrb r1, [r1, #2]
    cbz  r1, reject_setup
    push {r4, lr}
    bl   select_alternate
    pop.w {r4, lr}
    cbz  r0, reject_setup
    b    acknowledge_setup

get_interface:
    ldrh r2, [r0, #2]
    cbnz r2, reject_setup
    ldrh r2, [r0, #6]
    cmp  r2, #1
    bne  reject_setup
    ldrh r2, [r0, #4]
    cmp  r2, #2
    bhi  reject_setup
    movs r3, #0
    cmp  r2, #1
    bne  reply_interface
    ldr  r3, =STREAM
.if RATE_192
    ldrb r3, [r3, #HOST_ALTERNATE]
.else
    ldr  r3, [r3, #REQUESTED]
.if HIGH_SPEED
    uxtb r3, r3
    cbz  r3, reply_interface
    ldr  r2, =USB_CLASS
    ldrb r2, [r2]
    cmp  r2, #3
    itt  eq
    subeq r3, #1
    andeq r3, r3, #1
    it   eq
    addeq r3, #1
.endif
.endif
reply_interface:
    push {r3, lr}
    movs r0, #1
    mov  r1, sp
    bl   0x0307eb72
    movs r0, #0
    pop  {r3, pc}

set_configuration:
    ldrh r2, [r0, #4]
    cbnz r2, reject_setup
    ldrh r2, [r0, #6]
    cbnz r2, reject_setup
    ldrh r0, [r0, #2]
    cmp  r0, #1
    bhi  reject_setup
    push {r4, lr}
    mov  r4, r0
    movs r0, #0
    bl   select_alternate
    cbz  r0, config_failed
    ldr  r0, =STATE
    strb r4, [r0, #2]
    ldr  r0, =CONFIGURED
    strb r4, [r0]
    cbz  r4, config_ack
    movs r0, #0x83
    ldr  r1, =HID_EP_IN
    bl   0x0307eb00
config_ack:
    pop.w {r4, lr}
acknowledge_setup:
    push {r4, lr}
    bl   0x0307eeae
    movs r0, #0
    pop  {r4, pc}
config_failed:
    pop.w {r4, lr}
reject_setup:
    ldr  r0, =USB
    ldr  r1, [r0, #DIEPCTL0]
    orr  r1, r1, #0x200000
    str  r1, [r0, #DIEPCTL0]
    movs r0, #0
    bx   lr

select_alternate:
    push {r4, r5, r6, lr}
    mov  r4, r0
.if RATE_192
    ldr  r6, =STREAM
    ldrb r5, [r6, #HOST_ALTERNATE]
.else
    ldr  r0, =STREAM
    ldrb r5, [r0, #REQUESTED]
.endif
    cmp  r4, r5
    beq  alternate_selected
    bl   stop_endpoint
    cbz  r0, alternate_failed
.if RATE_192
    strb r4, [r6, #HOST_ALTERNATE]
.endif
    mov  r0, r4
    bl   arm_endpoint
.if RATE_192
    ldr  r1, =USB_CLASS
    ldrb r1, [r1]
    mov  r0, r4
    cmp  r1, #3
    bne  publish_selection
    mov  r1, r6
    bl   high_speed_mode
publish_selection:
    mov  r4, r0
.else
    mov  r0, r4
.endif
    bl   publish_alternate
.if DIAGNOSTICS
    mov  r0, r4
    bl   diag_select
.endif
alternate_selected:
    movs r0, #1
    pop  {r4, r5, r6, pc}
alternate_failed:
    mov  r0, r5
    bl   arm_endpoint
    movs r0, #0
    pop  {r4, r5, r6, pc}

.if RATE_192
arm_current_endpoint:
    ldr  r0, =STREAM
    ldrb r0, [r0, #HOST_ALTERNATE]
.endif
arm_endpoint:
.if HIGH_SPEED
    ldr  r1, =USB_CLASS
    ldrb r1, [r1]
    cmp  r1, #3
.if RATE_192
    beq  arm_high_speed_alternate
    ldr  r1, =MODES
.else
    ite  eq
    ldreq r1, =HS_MODES
    ldrne r1, =MODES
.endif
.else
    ldr  r1, =MODES
.endif
    add.w r1, r1, r0, lsl #MODE_SHIFT
    ldrh r2, [r1, #4]
    ldr  r3, =PACKET_SIZE
    strh r2, [r3]
    cbz  r0, no_endpoint
.if HIGH_SPEED && !RATE_192
    beq  arm_endpoint_high
.endif
    adds r1, #8
    movs r0, #2
    b.w  0x0307eb00
no_endpoint:
    bx   lr

.if RATE_192
arm_high_speed_alternate:
    adr  r1, high_speed_packet_limits
    ldrh r2, [r1, r0, lsl #1]
    ldr  r3, =PACKET_SIZE
    strh r2, [r3]
    cbz  r0, no_high_speed_endpoint
    b    arm_endpoint_high
no_high_speed_endpoint:
    bx   lr

// Map host alternate and clock to the internal PCM format. Hosts can
// issue clock and interface requests in either order during a transition.
high_speed_mode:
    cbz  r0, high_speed_mode_done
    ldrb r2, [r1, #RATE_OFFSET]
.if PCM_32
    cmp  r0, #5
    bne  high_speed_legacy_mode
    lsrs r0, r2, #1
    adds r0, #7
    bx   lr
high_speed_legacy_mode:
.endif
    cmp  r0, #2
    bhi  high_speed_mode_index
    cmp  r2, #4
    itt  eq
    moveq r0, #0
    bxeq lr
high_speed_mode_index:
    subs r0, #1
    and  r0, r0, #1
    adds r0, #1
    adds r0, r0, r2
high_speed_mode_done:
    bx   lr
.endif

.if HIGH_SPEED
arm_endpoint_high:
    ldr  r0, =USB
    ldr  r1, [r0, #DAINTMSK]
    orr  r1, r1, #0x40000
    str  r1, [r0, #DAINTMSK]
.if REVISED
    ldr  r1, [r0, #0x814]
    orr  r1, r1, #0x10
    str  r1, [r0, #0x814]
.endif
    movs r1, #0xff
    str  r1, [r0, #DOEPINT2]
    orr  r1, r2, #0x80000
    str  r1, [r0, #DOEPTSIZ2]
.if REVISED
    // The first host OUT token selects the service-interval parity.
    ldr  r1, =0x00048000
    orrs r1, r2
    str  r1, [r0, #DOEPCTL2]
    bx   lr
.else
    ldr  r1, =0x84048000
    orrs r1, r2
    b    next_microframe

// DSTS carries the current microframe parity at HS. An empty or missed
// transaction must not leave the software parity permanently inverted.
next_microframe:
    ldr  ip, [r0, #DSTS]
    tst.w ip, #0x100
    ite  eq
    orreq r1, r1, #0x20000000
    orrne r1, r1, #0x10000000
    str  r1, [r0, #DOEPCTL2]
    bx   lr
.endif
.endif

// Disarm EP2 OUT before changing MPS. Only its own stale FIFO entries and
// the controller's global OUT NAK notification can be drained here.
stop_endpoint:
    push {r4, r5, r6, lr}
    ldr  r4, =USB
    ldr  r0, [r4, #DAINTMSK]
    bic  r0, r0, #0x40000
    str  r0, [r4, #DAINTMSK]
    ldr  r0, [r4, #DOEPCTL2]
    tst  r0, #0x80000000
    beq  endpoint_stopped
    ldr  r0, [r4, #DCTL]
    bic  r0, r0, #0x600
    orr  r0, r0, #0x200
    str  r0, [r4, #DCTL]
    movw r5, #12000
wait_out_nak:
    ldr  r0, [r4, #0x14]
    tst  r0, #0x80
    bne  disable_out
    tst  r0, #0x10
    beq  next_out_nak
    ldr  r0, [r4, #0x1c]
    ubfx r1, r0, #17, #4
    cmp  r1, #1
    beq  pop_out_nak
    and  r2, r0, #15
    cmp  r2, #2
    bne  next_out_nak
    cmp  r1, #3
    beq  pop_out_nak
    cmp  r1, #2
    bne  next_out_nak
    ldr  r0, [r4, #0x20]
    ubfx r1, r0, #4, #11
    adds r1, #3
    lsrs r1, #2
    ldr  r2, =FIFO2
drain_old:
    cbz  r1, next_out_nak
    ldr  r0, [r2]
    subs r1, #1
    bne  drain_old
    b    next_out_nak
pop_out_nak:
    ldr  r0, [r4, #0x20]
next_out_nak:
    subs r5, #1
    bne  wait_out_nak
    b    endpoint_timeout
disable_out:
    ldr  r0, [r4, #DOEPCTL2]
    orr  r0, r0, #0x48000000
    str  r0, [r4, #DOEPCTL2]
    movw r5, #12000
wait_disabled:
    ldr  r0, [r4, #DOEPINT2]
    tst  r0, #2
    bne  clear_out_nak
    subs r5, #1
    bne  wait_disabled
endpoint_timeout:
    movs r6, #0
    b    finish_out_nak
clear_out_nak:
    movs r6, #1
finish_out_nak:
    ldr  r0, [r4, #DCTL]
    bic  r0, r0, #0x600
    orr  r0, r0, #0x400
    str  r0, [r4, #DCTL]
    cbz  r6, stop_failed
endpoint_stopped:
    ldr  r0, [r4, #DOEPCTL2]
    bic  r0, r0, #0x8000
    str  r0, [r4, #DOEPCTL2]
    movs r0, #0xff
    str  r0, [r4, #DOEPINT2]
    movs r0, #1
    pop  {r4, r5, r6, pc}
stop_failed:
    movs r0, #0
    pop  {r4, r5, r6, pc}

reset_event:
    push {r4, lr}
    mov  r4, r0
    movs r0, #0
.if RATE_192
    ldr  r1, =STREAM
    strb r0, [r1, #HOST_ALTERNATE]
.endif
    bl   publish_alternate
    mov  r0, r4
    pop.w {r4, lr}
    b.w  0x0307fca0

disconnect_event:
    push {r4, lr}
    mov  r4, r0
    movs r0, #0
.if RATE_192
    ldr  r1, =STREAM
    strb r0, [r1, #HOST_ALTERNATE]
.endif
    bl   publish_alternate
.if DIAGNOSTICS
    bl   diag_disconnect
.endif
    mov  r0, r4
    pop.w {r4, lr}
    b.w  0x0307fca8

receive_event:
.if DIAGNOSTICS
    cmp  r1, #0x10
    bne  diag_rx_dispatch
    push {r0, r1, r2, r3, r4, lr}
    bl   diag_connect
    pop.w {r0, r1, r2, r3, r4, lr}
diag_rx_dispatch:
.endif
    cmp  r0, #2
    bne  stock_event
    cmp  r1, #1
    beq  receive_audio
stock_event:
    b.w  0x0307fbbe

receive_audio:
    push.w {r4, r5, r6, r7, r8, lr}
.if !PACKED_192
    movs r4, #1
.endif
    ldr  r6, =STATE
    uxth r7, r2
    ldr  r0, =FIFO2
    ldr  r1, =STAGING
    adds r2, r7, #3
    lsrs r2, #2
    cbz  r2, packet_read
read_fifo:
    ldr  r5, [r0]
    str  r5, [r1], #4
    subs r2, #1
    bne  read_fifo
packet_read:
.if DIAGNOSTICS
    bl   diag_packet
.endif
    ldr  r5, =STREAM
    ldr  r8, [r5, #REQUESTED]
.if PACKED_192
    uxtb r4, r8
    mov  r3, r4
.else
    uxtb r3, r8
.endif
.if HIGH_SPEED
    cmp  r3, #0
    beq  discard_packet
    ldr  r0, =USB_CLASS
    ldrb r0, [r0]
    cmp  r0, #3
    ite  eq
    ldreq r0, =HS_MODES
    ldrne r0, =MODES
.else
    cbz  r3, discard_packet
    ldr  r0, =MODES
.endif
.if RATE_192
    ite  eq
    addeq.w r3, r0, r3, lsl #3
    addne.w r3, r0, r3, lsl #MODE_SHIFT
    bne  size_full_speed
    ldr  r0, =PACKET_SIZE
    ldrh r2, [r0]
    b    unpack_packet
size_full_speed:
    ldrh r2, [r3, #4]
.else
    add.w r3, r0, r3, lsl #MODE_SHIFT
    ldrh r2, [r3, #4]
.if REVISED
    beq  unpack_packet
.endif
.endif
    ldr  r0, =USB
    orr  r1, r2, #0x80000
    str  r1, [r0, #DOEPTSIZ2]
.if HIGH_SPEED
.if !REVISED
    beq  rearm_high_speed
.endif
.endif
    ldrb r1, [r6, #3]
    eor  r1, r1, #1
    strb r1, [r6, #3]
    ldr  r1, [r0, #DOEPCTL2]
    bic  r1, r1, #0x30000000
    ldrb.w ip, [r6, #3]
    tst.w ip, #1
    ite  ne
    orrne r1, r1, #0x20000000
    orreq r1, r1, #0x10000000
    orr  r1, r1, #0x04000000
    str  r1, [r0, #DOEPCTL2]
.if HIGH_SPEED
.if !REVISED
    b    unpack_packet
rearm_high_speed:
    ldr  r1, [r0, #DOEPCTL2]
    bic  r1, r1, #0x30000000
    orr  r1, r1, #0x04000000
    bl   next_microframe
.endif
unpack_packet:
.endif
    cmp  r7, r2
    bhi  discard_packet
    ldr  r1, [r5, #APPLIED]
    cmp  r1, r8
    bne  discard_packet
    ldrb r1, [r3, #6]
    udiv r7, r7, r1
    cbz  r7, discard_packet
.if PACKED_192
    cmp  r4, #6
.if PCM_32
    blo  expand_pcm
.else
    bne  expand_pcm
.endif
    lsls r7, #3
    b    samples_ready
expand_pcm:
.endif
    sub  sp, #8
    str  r7, [sp]
    lsls r7, #3
    ldr  r0, [r6, #0x4c]
    mov  r1, sp
    ldrb r2, [r3, #7]
    bl   0x0306613c
    add  sp, #8
.if PACKED_192
samples_ready:
.endif
.if DIAGNOSTICS
    bl   diag_ready
.endif
.if REVISED
    b    write_ring_words
.else
    // Original ring writer receives r7 bytes in its own staging area.
    b.w  0x0307fc3a
.endif
discard_packet:
    pop.w {r4, r5, r6, r7, r8, pc}

poll_audio:
    push {r4, r5, r6, lr}
    ldr  r4, =STATE
    ldr  r5, =STREAM
.if RECOVERY
    bl   0x0307fbd4
.endif
    ldr  r2, [r5, #REQUESTED]
    ldr  r3, [r5, #APPLIED]
    cmp  r2, r3
    beq  stream_ready
    ldr  r0, [r4, #0x2c]
    ldr  r1, [r4, #0x30]
    orrs r0, r1
    beq  reset_ring
.if REVISED
    movs r0, #4
    strh r0, [r4, #6]
    movs r0, #3
    strh r0, [r4, #8]
.else
    bl   0x03080382
.endif
    movs r0, #5
    bl   0x03055ebc
    cbnz r0, audio_idle
.if REVISED
    bl   0x0306656e
.endif
reset_ring:
    mrs  r6, primask
    cpsid i
    movs r0, #0
    strb r0, [r4]
    strb r0, [r4, #1]
    strh r0, [r4, #8]
    str  r0, [r4, #0x10]
    str  r0, [r4, #0x14]
    str  r0, [r4, #0x18]
    str  r0, [r4, #0x1c]
    str  r0, [r4, #0x28]
    str  r0, [r4, #0x2c]
    str  r0, [r4, #0x30]
    str  r0, [r4, #0x38]
    str  r0, [r4, #0x44]
    ldr  r3, [r5, #REQUESTED]
    str  r3, [r5, #APPLIED]
    msr  primask, r6
.if DIAGNOSTICS
    bl   diag_report
    ldr  r3, [r5, #APPLIED]
.endif
stream_ready:
    uxtb r3, r3
    cbz  r3, audio_idle
    pop.w {r4, r5, r6, lr}
    push.w {r4, r5, r6, r7, r8, lr}
    b.w  0x03080200
audio_idle:
    movs r0, #0
    pop  {r4, r5, r6, pc}

monitor_audio:
    ldr  r1, =STREAM
    ldr  r2, [r1, #REQUESTED]
    ldr  r3, [r1, #APPLIED]
    cmp  r2, r3
    bne  skip_monitor
    uxtb r3, r3
    cbz  r3, skip_monitor
    push {r4, lr}
    ldr  r4, =STATE
.if REVISED
    ldrb r0, [r4]
    cmp  r0, #1
    bne  check_resume
    bl   0x0307fe14
    cmp.w r0, #0x3000
    bhs  monitor_done
    movs r0, #2
    strb r0, [r4]
    movs r0, #4
    strh r0, [r4, #6]
    movs r0, #3
    strh r0, [r4, #8]
    b    monitor_done
check_resume:
    cmp  r0, #2
    bne  monitor_done
    movs r0, #5
    bl   0x03055ebc
    cbnz r0, monitor_done
    bl   0x0307fe14
    cmp.w r0, #0x6c00
    bls  monitor_done
    movs r0, #1
    strb r0, [r4]
    bl   0x030803f0
monitor_done:
    movs r0, #0
    pop  {r4, pc}
.else
    b.w  0x03080464
.endif
skip_monitor:
    movs r0, #0
    bx   lr

initialise_pcm:
    ldr  r0, =STREAM
    ldrb r1, [r0, #APPLIED]
.if HIGH_SPEED
    ldr  r0, =USB_CLASS
    ldrb r0, [r0]
    cmp  r0, #3
    ite  eq
    ldreq r0, =HS_MODES
    ldrne r0, =MODES
.else
    ldr  r0, =MODES
.endif
.if RATE_192
    ite  eq
    addeq.w r0, r0, r1, lsl #3
    addne.w r0, r0, r1, lsl #MODE_SHIFT
.else
    add.w r0, r0, r1, lsl #MODE_SHIFT
.endif
    ldr  r2, [r0]
    ldrb r1, [r0, #7]
    lsls r3, r2, #3
    ldr  r0, =PCM_INFO
    strd r2, r3, [r0, #8]
.if RATE_192
    ldr  r3, =192000
    cmp  r2, r3
    ittt eq
    ldreq r2, =STATE
    moveq.w r3, #1024
    streq r3, [r2, #0x40]
.endif
    movs r2, #2
    str  r2, [r0, #0x10]
    str  r1, [r0, #0x9c]
    bx   lr

.if CLOCK_FIX
// Reuse the 48 kHz PLL and MCLK. At 96 kHz halve the I2S master
// divider from four to two, keeping 64 SCLK periods per stereo frame.
// The jump from the stock caller keeps its three stacked arguments intact.
configure_i2s:
    movw r3, #48000
    bl   0x030560a8
    ldr  r0, [r5, #8]
    movw r1, #48000
    cmp  r0, r1
    beq  i2s_configured
    ldr  r0, =I2S1_CKR
    ldr  r1, [r0]
    bic  r1, r1, #0xff0000
    orr  r1, r1, #0x10000
    str  r1, [r0]
i2s_configured:
    b.w  0x030800ac
.endif

.if HIGH_SPEED
dual_speed_descriptor:
    ldrh r2, [r0, #2]
.if REVISED
.if RECOVERY
    subw ip, r2, #0x304
    cmp  ip, #1
    bls.w diagnostic_detail
.else
    cmp.w r2, #0x304
    beq.w diagnostic_detail
.endif
    movw ip, #0x303
    cmp  r2, ip
    bne  check_device_descriptor
    push {r0, r1, r4, lr}
    bl   diag_report
    pop  {r0, r1, r4, lr}
    b.w  stock_setup_request
check_device_descriptor:
.endif
    cmp.w r2, #0x100
    beq  device_descriptor
    cmp.w r2, #0x600
    bne  stock_setup_request
    ldrb r2, [r1]
    cmp  r2, #3
    ite  eq
    adreq r1, fs_qualifier
    adrne r1, hs_qualifier
    movs r2, #10
    b    send_control
device_descriptor:
    ldr  r1, [r1, #8]
    movs r2, #18
    b    send_control

uac2_control:
    ldrh r3, [r0, #4]
    cmp  r3, #2                  // HID still uses the stock class handler.
    beq  stock_setup_request
    cmp.w r3, #0x400
.if HOST_VOLUME
    bne.w stock_setup_request   // Feature Unit and HID share the class handler.
.else
    bne.w reject_setup
.endif
    ldrh r3, [r0, #2]
    cmp.w r3, #0x100
    bne  check_clock_valid
    ldrb r3, [r0, #1]
    cmp  r2, #0xa1
    beq  get_clock_rate
    cmp  r3, #1                  // SET_CUR, uint32 sample rate
    bne.w reject_setup
    ldrh r3, [r0, #6]
    cmp  r3, #4
    bne.w reject_setup
    push {r4, lr}
    bl   0x0307f236
    movs r1, #4
    strh r1, [r0]
    movs r1, #0
    strb r1, [r0, #12]
    adr  r1, clock_set_complete
    orr  r1, r1, #1
    str  r1, [r0, #8]
    bl   0x0307ee00
    movs r0, #0
    pop  {r4, pc}

check_clock_valid:
    cmp.w r3, #0x200
    bne.w reject_setup
    cmp  r2, #0xa1
    bne.w reject_setup
    ldrb r3, [r0, #1]
    cmp  r3, #1
    bne.w reject_setup
    adr  r1, clock_valid
    movs r2, #1
    b    send_control
get_clock_rate:
    cmp  r3, #2                  // GET_RANGE
    beq  clock_range_reply
    cmp  r3, #1                  // GET_CUR
    bne.w reject_setup
    ldr  r3, =STREAM
    ldrb r3, [r3, #RATE_OFFSET]
    adr  r1, clock_rates
    add.w r1, r1, r3, lsl #1
    movs r2, #4
    b    send_control
clock_range_reply:
    adr  r1, clock_range
.if RATE_192
    movs r2, #38
.else
    movs r2, #26
.endif
send_control:
    ldrh r0, [r0, #6]
    cmp  r0, r2
    it   hi
    movhi r0, r2
    push {r4, lr}
    bl   0x0307eb72
    movs r0, #0
    pop  {r4, pc}

clock_set_complete:
    push {r4, lr}
    bl   0x0307f236
    ldr  r0, [r0, #4]
    ldr  r0, [r0]
    movw r1, #48000
    movs r2, #0
    cmp  r0, r1
    beq  publish_clock_rate
    ldr  r1, =96000
.if RATE_192
    movs r2, #2
    cmp  r0, r1
    beq  publish_clock_rate
    ldr  r1, =192000
    cmp  r0, r1
    bne  clock_unchanged
    movs r2, #4
.else
    cmp  r0, r1
    bne  clock_unchanged
    movs r2, #2
.endif
publish_clock_rate:
    ldr  r1, =STREAM
.if RATE_192
    ldrb r3, [r1, #RATE_OFFSET]
    cmp  r2, r3
    beq  clock_unchanged
.endif
    strb r2, [r1, #RATE_OFFSET]
.if RATE_192
    ldrb r0, [r1, #HOST_ALTERNATE]
    bl   high_speed_mode
.else
    ldrb r0, [r1, #REQUESTED]
    cbz  r0, clock_unchanged
    subs r0, #1
    and  r0, r0, #1
    adds r0, #1
    adds r0, r0, r2
.endif
    mov  r4, r0
    bl   publish_alternate
    mov  r0, r4
    bl   diag_select
clock_unchanged:
    pop  {r4, pc}

.balign 4
fs_qualifier:
    .byte 10, 6, 0, 2, 0, 0, 0, 64, 1, 0, 0, 0
hs_qualifier:
    .byte 10, 6, 0, 2, 0xef, 2, 1, 64, 1, 0, 0, 0
clock_valid:
    .word 1
clock_rates:
    .word 48000, 96000
.if RATE_192
    .word 192000
clock_range:
    .short 3
    .word 48000, 48000, 0, 96000, 96000, 0, 192000, 192000, 0
    .short 0
high_speed_packet_limits:
    .short __HS_PACKET_LIMITS__
.else
clock_range:
    .short 2
    .word 48000, 48000, 0, 96000, 96000, 0
    .short 0
.endif
.endif

elapsed_ms:
    ldr  r0, =STATE
    ldr  r0, [r0, #0x44]
    ldr  r1, =PCM_INFO
    ldr  r1, [r1, #8]
.if RATE_192
    movw r2, #500
    udiv r1, r1, r2
.else
    movw r2, #48000
    cmp  r1, r2
    ite  hi
    movhi r1, #192
    movls r1, #96
.endif
    udiv r0, r0, r1
    bx   lr

dma_start_gate:
    ldrh r0, [r5, #8]
    cbnz r0, skip_dma_start
    ldr  r0, =STREAM
    ldr  r1, [r0, #REQUESTED]
    ldr  r0, [r0, #APPLIED]
    cmp  r0, r1
    bne  skip_dma_start
    uxtb r0, r0
    cbz  r0, skip_dma_start
.if DMA_PIPELINE
    push {r4, lr}
    bl   prime_dma
    pop.w {r4, lr}
.endif
.if DIAGNOSTICS
    bl   diag_clock
.endif
    b.w  0x0308011a
skip_dma_start:
    b.w  0x03080148

.if DMA_PIPELINE
// Prepare one full block while the other half is free or playing.
prepare_dma:
    push {r4, r5, r6, lr}
    ldr  r4, =STATE
    ldr  r5, =DMA_BUFFER
    ldr  r6, [r4, #0x3c]
    add.w r5, r5, r6, lsl #DMA_HALF_SHIFT
    mov  r0, r5
.if RATE_192
    ldr  r6, [r4, #0x40]
    lsls r6, #2
    mov  r1, r6
    bl   0x0307fe2e
    subs r1, r6, r0
.else
    mov.w r1, #0x800
    bl   0x0307fe2e
    rsbs.w r1, r0, #0x800
.endif
    beq  prepared_block
    add  r0, r5
    bl   0x0305531c
prepared_block:
    mov  r0, r5
    add.w r1, r4, #0x24
    bl   0x0307fd7c
    pop  {r4, r5, r6, pc}

prime_dma:
    ldr  r0, =PCM_INFO
    ldr  r0, [r0, #8]
    ldr  r1, =96000
    cmp  r0, r1
.if RATE_192
    bhs  prepare_dma
.else
    beq  prepare_dma
.endif
    bx   lr

prime_recovery:
    ldr  r0, =PCM_INFO
    ldr  r0, [r0, #8]
    ldr  r1, =96000
    cmp  r0, r1
.if RATE_192
    bhs  prepare_dma
.else
    beq  prepare_dma
.endif
    b.w  0x0307fe6e

dma_complete:
    ldr  r0, =PCM_INFO
    ldr  r0, [r0, #8]
    ldr  r1, =96000
    cmp  r0, r1
.if RATE_192
    bhs  dma_complete_high_rate
.else
    beq  dma_complete_high_rate
.endif
    push {r4, lr}
    sub  sp, #8
    b.w  0x0307fee2
dma_complete_high_rate:
    push {r4, r5, r6, lr}
    sub  sp, #8
    ldr  r4, =STATE
    ldrh r0, [r4, #6]
    cbnz r0, dma_completion_done
    ldr  r5, [r4, #0x3c]
    ldr  r1, =DMA_BUFFER
    add.w r1, r1, r5, lsl #DMA_HALF_SHIFT
    str  r1, [r4, #0x20]
    ldr  r0, =0x03024c54
    ldr  r2, =0x0307fedf
    strd r0, r2, [sp]
    ldr  r2, =0x40030024
    ldr  r3, [r4, #0x40]
    str  r3, [r4, #0x24]
    movs r0, #5
    // Reuse the channel setup and hand the already prepared buffer to DMA.
    bl   0x030602f6
    ldrd r0, r1, [r4, #0x40]
    adds r0, r0, r1
    str  r0, [r4, #0x44]
    eor  r5, r5, #1
    str  r5, [r4, #0x3c]
    bl   prepare_dma
    bl   diag_dma
dma_completion_done:
    add  sp, #8
    pop  {r4, r5, r6, pc}
.endif

// Existing ring reader handles the wrap and counters. Clear a short block
// before DMA so the tail cannot replay samples from an earlier block.
fill_dma:
    push {r4, r5, r6, lr}
.if DIAGNOSTICS
    bl   diag_dma
.endif
    ldr  r4, =STATE
    ldr  r5, =DMA_BUFFER
    ldr  r6, [r4, #0x3c]
    add.w r5, r5, r6, lsl #DMA_HALF_SHIFT
    mov  r0, r5
    mov.w r1, #0x800
    bl   0x0307fe2e
    cbz  r0, no_ring_data
    rsbs.w r1, r0, #0x800
    beq  block_filled
    add  r0, r5
    bl   0x0305531c
block_filled:
    ldrd r0, r1, [r4, #0x40]
    adds r0, r0, r1
    str  r0, [r4, #0x44]
    eor  r6, r6, #1
    str  r6, [r4, #0x3c]
    movs r0, #1
    pop  {r4, r5, r6, pc}
no_ring_data:
    ldr  r0, =DMA_BUFFER
.if RATE_192
    mov.w r1, #0x2000
.else
    mov.w r1, #0x1000
.endif
    bl   0x0305531c
    movs r0, #0
    pop  {r4, r5, r6, pc}

.if DIAGNOSTICS
// String index 3 is unused by the UAC device descriptor. Its eight
// UTF-16 words carry bounded status counters, without any audio data.
diag_connect:
.if HIGH_SPEED
    // The HS configuration uses an IAD, with legacy device class at FS.
    ldr  r0, [r3, #8]
    movw r1, #0x0200
    strh r1, [r0, #2]
    cmp  r2, #3
    ite  eq
    ldreq r1, =0x400102ef
    movne r1, #0x40000000
    str  r1, [r0, #4]
.endif
    ldr  r1, =STREAM
.if HIGH_SPEED
    movs r0, #0
    strb r0, [r1, #RATE_OFFSET]
.endif
.if HOST_VOLUME
    // Clear the controls and gain for a fresh enumeration. The two words
    // match HOST_STATE in uac_host_volume.S.
    str  r0, [r1, #60]
    str  r0, [r1, #64]
.endif
    movs r0, #1
    strb r0, [r1, #DIAG_ENABLED]
    b    diag_report

diag_disconnect:
    ldr  r1, =STREAM
    movs r0, #0
    strb r0, [r1, #DIAG_ENABLED]
    ldr  r1, =DIAG_STRING
    ldr  r0, =0x00530055
    str  r0, [r1]
    ldr  r0, =0x00560042
    str  r0, [r1, #4]
    ldr  r0, =0x002e0031
    str  r0, [r1, #8]
    ldr  r0, =0x00300030
    str  r0, [r1, #12]
    bx   lr

diag_select:
    cbz  r0, diag_report_gate
    ldr  r1, =STREAM
.if !PCM_32
    strb r0, [r1, #DIAG_ALTERNATE]
.endif
    movs r0, #0
    strb r0, [r1, #DIAG_CLOCKED]
    str  r0, [r1, #DIAG_RX]
    str  r0, [r1, #DIAG_MAX]
    str  r0, [r1, #DIAG_READY]
    str  r0, [r1, #DIAG_DMA]
diag_report_gate:
    b    diag_report

diag_packet:
    ldr  r1, =STREAM
    ldrb r0, [r1, #REQUESTED]
    cmp  r0, #0
.if REVISED
    beq  diag_counter_done
.else
    beq  diag_report
.endif
    ldr  r0, [r1, #DIAG_RX]
    adds r0, #1
    str  r0, [r1, #DIAG_RX]
    ldr  r0, [r1, #DIAG_MAX]
    cmp  r7, r0
    it   hi
    strhi r7, [r1, #DIAG_MAX]
.if REVISED
diag_counter_done:
    bx   lr
.else
    b    diag_report
.endif

diag_ready:
    ldr  r1, =STREAM
    ldr  r0, [r1, #DIAG_READY]
    adds r0, #1
    str  r0, [r1, #DIAG_READY]
.if REVISED
    bx   lr
.else
    b    diag_report
.endif

diag_clock:
    ldr  r1, =STREAM
    movs r0, #1
    strb r0, [r1, #DIAG_CLOCKED]
.if REVISED
    bx   lr
.else
    b    diag_report
.endif

diag_dma:
    ldr  r1, =STREAM
    ldrb r0, [r1, #APPLIED]
    cmp  r0, #0
.if REVISED
    beq  diag_counter_done
.else
    beq  diag_report
.endif
    ldr  r0, [r1, #DIAG_DMA]
    adds r0, #1
    str  r0, [r1, #DIAG_DMA]
.if REVISED
    bx   lr
.endif

diag_report:
    ldr  r0, =STREAM
    ldrb r1, [r0, #DIAG_ENABLED]
    cbz  r1, report_done
    push {r4, r5, r6, lr}
    ldr  r4, =USB
    ldr  r5, =DIAG_STRING

    ldrb r1, [r0, #REQUESTED]
    ldrb r2, [r0, #APPLIED]
    orr.w r1, r1, r2, lsl #DIAG_MODE_BITS
.if !PCM_32
    ldrb r2, [r0, #DIAG_ALTERNATE]
    orr.w r1, r1, r2, lsl #6
.endif
    ldrb r2, [r0, #DIAG_CLOCKED]
    orr.w r1, r1, r2, lsl #DIAG_CLOCK_SHIFT
    ldr  r2, [r4, #0x48]
    ubfx r2, r2, #19, #1
    orr.w r1, r1, r2, lsl #(DIAG_CLOCK_SHIFT + 1)
    orr  r1, r1, #0xe800
    strh r1, [r5], #2

    ldr  r1, [r4, #0x24]
    ubfx r1, r1, #0, #12
    orr  r1, r1, #0xe000
    strh r1, [r5], #2
    ldr  r1, [r4, #0x4c]
    ubfx r1, r1, #16, #12
    orr  r1, r1, #0xe000
    strh r1, [r5], #2
    ldr  r1, [r4, #0x28]
    ubfx r1, r1, #0, #12
    orr  r1, r1, #0xe000
    strh r1, [r5], #2

    add.w r4, r0, #DIAG_RX
    movs r6, #4
diag_counters:
    ldr  r1, [r4], #4
    ubfx r1, r1, #0, #12
    orr  r1, r1, #0xe000
    strh r1, [r5], #2
    subs r6, #1
    bne  diag_counters
    pop  {r4, r5, r6, pc}
report_done:
    bx   lr
.endif

.if REVISED
// A delayed 500 us service interval keeps its initial frame parity.
// The common IRQ handler reaches this entry only for OUTTKNEPDIS.
out_token_disabled:
    push {r1, r2}
    ldr  r2, [r0, #-0x38]
    ldr  r1, [r0, #0x300]
    bic  r1, r1, #0x30000000
    tst  r2, #0x100
    ite  ne
    orrne r1, r1, #0x20000000
    orreq r1, r1, #0x10000000
    orr  r1, r1, #0x84000000
    str  r1, [r0, #0x300]
    movs r1, #0x10
    str  r1, [r0, #0x308]
    pop  {r1, r2}
    b.w  0x0307f72e

write_ring_words:
    ldrd r0, r1, [r6, #0x14]
    subs r2, r1, r0
    it   le
    addle.w r2, r2, #0x9000
    subs r2, #8
    cmp  r2, r7
    blo  ring_full
    ldr  r1, =STAGING
    ldr  r2, =RING
.if PACKED_192
    cmp  r4, #6
    bne  write_word
    bl   __PACKED_192_WRITER__
    b    publish_ring_write
.endif
write_word:
    ldr  ip, [r1], #4
    str  ip, [r2, r0]
    adds r0, #4
    cmp.w r0, #0x9000
    it   eq
    moveq r0, #0
    subs r7, #4
    bne  write_word
.if PACKED_192
publish_ring_write:
.endif
    str  r0, [r6, #0x14]
    b.w  discard_packet
ring_full:
    ldr  r1, =STREAM
    ldr  r0, [r1, #RX_DROPPED]
    adds r0, #1
    str  r0, [r1, #RX_DROPPED]
    b.w  discard_packet

read_ring_words:
    orr  r3, r0, r1
    tst  r3, #7
    beq  read_aligned
    push {r4, r5, lr}
    mov  r2, r1
    b.w  0x0307fe32
read_aligned:
    push {r4, r5, r6, lr}
    mov  ip, r0
    ldr  r4, =STATE
    ldrd r3, r5, [r4, #0x14]
    subs r3, r3, r5
    it   mi
    addmi.w r3, r3, #0x9000
    cmp  r3, r1
    it   hi
    movhi r3, r1
    ldr  r6, =STREAM
    bhs  read_block_ready
    ldr  r2, [r6, #DMA_SHORT]
    adds r2, #1
    str  r2, [r6, #DMA_SHORT]
read_block_ready:
    mov  r0, r3
    movs r2, #0
    cmp  r3, #0
    beq  words_read
    ldr  r6, =RING
read_word:
    ldr  r1, [r6, r5]
    orrs r2, r1
    str  r1, [ip], #4
    adds r5, #4
    cmp.w r5, #0x9000
    it   eq
    moveq r5, #0
    subs r3, #4
    bne  read_word
words_read:
    str  r5, [r4, #0x18]
    ldr  r1, [r4, #0x1c]
    adds r1, r1, r0
    str  r1, [r4, #0x1c]
    ldr  r6, =STREAM
    str  r2, [r6, #DMA_MASK]
    ldr  r1, [r4, #0x14]
    subs r1, r1, r5
    it   mi
    addmi.w r1, r1, #0x9000
    str  r1, [r6, #RING_LEVEL]
    pop  {r4, r5, r6, pc}

diagnostic_detail:
    push {r0, r4, r5, r6, r7, lr}
.if RECOVERY
    ldrb r1, [r0, #2]
    cmp  r1, #5
    ite  eq
    adreq r4, usb_detail_sources
    adrne r4, detail_sources
.else
    adr  r4, detail_sources
.endif
    ldr  r5, =DETAIL_STRING
    movw r1, #0x033e
    strh r1, [r5], #2
    movs r6, #10
detail_field:
    ldr  r1, [r4], #4
    ldr  r1, [r1]
    movs r7, #3
detail_digit:
    ubfx r2, r1, #0, #12
    orr  r2, r2, #0xe000
    strh r2, [r5], #2
    lsrs r1, #12
    subs r7, #1
    bne  detail_digit
    subs r6, #1
    bne  detail_field
    pop  {r0, r4, r5, r6, r7, lr}
    ldr  r1, =DETAIL_STRING
    movs r2, #62
    b.w  send_control
.balign 4
detail_sources:
    .word PCM_INFO + 8, PCM_INFO + 0x9c, I2S1_CKR, 0x40030000
    .word STREAM + RING_LEVEL, STREAM + RX_DROPPED, STREAM + DMA_SHORT
    .word STREAM + DMA_MASK, 0x03024b60, USB + DOEPCTL2
.if RECOVERY
usb_detail_sources:
    .word USB + 0x14, USB + 0x18, USB + DOEPINT2, USB + 0x814
    .word USB + DOEPTSIZ2, USB + DSTS, USB + DCTL
    .word STREAM + 48, STREAM + 52, STREAM + 56
.endif
.endif

.ltorg

