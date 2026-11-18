.syntax unified
.cpu cortex-m3
.thumb

// r0 and r7 are the aligned ring offset and byte count. r1 points to the
// received packed PCM in SRAM and r2 to the ring. The caller publishes r0
// only after complete stereo frames have been written.
write_packed_stereo:
    ldr  r3, [r1]
    ldr.w ip, [r1, #3]
    lsls r3, #8
    lsl.w ip, ip, #8
    add.w r4, r2, r0
    strd r3, ip, [r4]
    adds r1, #6
    adds r0, #8
    cmp.w r0, #0x9000
    it   eq
    moveq r0, #0
    subs r7, #8
    bne  write_packed_stereo
    bx   lr
