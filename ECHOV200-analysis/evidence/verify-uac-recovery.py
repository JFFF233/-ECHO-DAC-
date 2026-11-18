
from pathlib import Path
import sys

firmware_path=Path(sys.argv[1]) if len(sys.argv)>1 else Path("UAC-96-recovery-test/ECHOV200.IMG")
base=Path("ECHOV200-analysis/evidence/verify-uac-revised.py").read_text()
exec(compile(base,"verify-uac-recovery-base","exec"))
# Each poll returns while the controller completes its NAK and disable steps.
set_speed(True)
put(STREAM,4,4)
if rate_192:
    # Mode 6 is 24/192. The watchdog must rearm host alternate 4.
    put(STREAM,6,6)
    cpu.mem_write(STREAM+29,b"\x04")
    mps=300
else:
    mps=294
put(STREAM+12,123)
put(STREAM+48,0,0,0)
put(0x03022330,1000)
execute(0x0307fbd4)
assert word(STREAM+48)==123 and word(STREAM+52)==1000 and word(STREAM+56)==0
put(0x03022330,1004)
execute(0x0307fbd4)
assert word(STREAM+56)==0
put(0x03022330,1005)
execute(0x0307fbd4)
assert word(STREAM+56)==1 and word(USB+0x804)&0x200
put(0x03022330,1006)
execute(0x0307fbd4)
assert word(STREAM+56)==2 and (word(MASK)&0x40000)==0
put(0x03022330,1007)
execute(0x0307fbd4)
assert word(STREAM+56)==0 and word(MASK)&0x40000
assert word(CTRL)&0x7ff==mps and word(SIZE)==0x80000+mps
assert word(CTRL)&0x80000000==0 and word(USB+0x804)&0x400
execute(0x0307f710,USB+0x840,4,0x10,2,stop=0x0307f72e)
assert word(CTRL)&0x80000000
before=word(STREAM+12)
rx(bytes(288),mps)
assert word(STREAM+12)==before+1
# Missing hardware acknowledgement clears the request after a bounded interval.
ack_out_nak=False
put(USB+0x14,0)
put(STREAM+48,word(STREAM+12),2000,0)
put(0x03022330,2006)
execute(0x0307fbd4)
assert word(STREAM+56)==1
put(0x03022330,2007)
execute(0x0307fbd4)
assert word(STREAM+56)==1
put(0x03022330,2107)
execute(0x0307fbd4)
assert word(STREAM+56)==0 and word(USB+0x804)&0x400
ack_out_nak=True
# A pending format generation cancels recovery.
put(STREAM+56,1)
put(STREAM,0x104,4)
execute(0x0307fbd4)
assert word(STREAM+56)==0 and word(USB+0x804)&0x400
put(STREAM,4,4)
put(STREAM+56,0)
for tick in range(3000,3020):
    put(0x03022330,tick)
    put(STREAM+12,word(STREAM+12)+2)
    execute(0x0307fbd4)
    assert word(STREAM+56)==0
# Read the fixed USB register page using the real descriptor dispatcher.
request(0x80,6,0x0305,0x0409,62)
assert events[-1][0]=="send" and len(events[-1][1])==62
print("USB watchdog: healthy traffic, stalled OUT, asynchronous NAK/disable/rearm, timeout, generation cancellation and register page passed.",flush=True)

