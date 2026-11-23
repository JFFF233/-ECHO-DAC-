
from pathlib import Path
from struct import pack, unpack, unpack_from
import sys
sys.path.insert(0, str(Path.cwd()))
sys.path.insert(0, str(Path(".toolchain/python").absolute()))
from unicorn import (
    Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE,
    UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UC_HOOK_MEM_INVALID,
)
from unicorn.arm_const import (
    UC_CPU_ARM_CORTEX_M3, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
    UC_ARM_REG_R3, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_FP,
    UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_PRIMASK,
)
from patch_echo_uac_multiformat import DMA_BUFFER_192, MODULE_RECORD

if "firmware_path" not in globals():
    firmware_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("UAC-96-high-speed-revised/ECHOV200.IMG")
image = firmware_path.read_bytes()
revision = unpack_from("<H", image, 0x8084C)[0]
assert revision in (0x0206, 0x0207, 0x0208, 0x0209, 0x0210, 0x0211, 0x0212)
rate_192 = revision >= 0x0208
pcm_32 = revision >= 0x0210
host_volume = revision >= 0x0212
base = unpack_from("<I", image, 0x200)[0]
load, run_address, length = unpack_from("<III", image, MODULE_RECORD)
cpu = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
cpu.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_M3)
cpu.mem_map(0x03000000, 0x100000)
cpu.mem_map(0x40030000, 0x1000)
cpu.mem_map(0x60080000, 0x10000)
for offset, address, size in [
    (0x297cc, 0x03050000, 178416),
    (0x590bc, 0x03004000, 2652),
    (0x7eaa4, 0x0307e8c0, 4216),
    (0x7fb1c, 0x03024898, 130),
    (load-base+0x200, run_address, length),
    (0x8073c, 0x03024b60, 279),
]:
    cpu.mem_write(address,image[offset:offset+size])

STATE=0x03024c78
STREAM=0x0302f588
RING=0x03024ce8
MUSIC=0x0302dce8
DMA_BUFFER=DMA_BUFFER_192 if rate_192 else 0x0302dd88
DMA_HALF_SIZE=4096 if rate_192 else 2048
USB_CLASS=0x03024c1c
USB=0x60080000
FIFO=USB+0x3000
CTRL=USB+0xb40
INT=USB+0xb48
SIZE=USB+0xb50
MASK=USB+0x81c
SP=0x03003e00
END=0x03003c00
REQUEST_BUF=0x03003800
REGS=(UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3)

def read(ptr,n):
    return bytes(cpu.mem_read(ptr,n))

def put(ptr,*values):
    cpu.mem_write(ptr,pack("<"+"I"*len(values),*values))

def word(ptr):
    return unpack("<I",read(ptr,4))[0]

def half(ptr):
    return unpack("<H",read(ptr,2))[0]

def invalid(uc,kind,address,size,value,ignored):
    print(f"MMIO unmapped {kind} {address:#x} @{uc.reg_read(UC_ARM_REG_PC):#x}",flush=True)
    return False
cpu.hook_add(UC_HOOK_MEM_INVALID,invalid)

def execute(entry,*args,stop=END,stack=()):
    for reg,value in zip(REGS,list(args)+[0]*(4-len(args))):
        cpu.reg_write(reg,value)
    cpu.reg_write(UC_ARM_REG_SP,SP)
    cpu.reg_write(UC_ARM_REG_LR,END|1)
    cpu.reg_write(UC_ARM_REG_PRIMASK,0)
    if stack:
        put(SP,*stack)
    cpu.emu_start(entry|1,stop,count=300000)
    assert cpu.reg_read(UC_ARM_REG_PC)==stop, (hex(entry),hex(cpu.reg_read(UC_ARM_REG_PC)))
    if stop==END:
        assert cpu.reg_read(UC_ARM_REG_SP)==SP
    return cpu.reg_read(UC_ARM_REG_R0)

# Hardware callbacks are modelled separately from the firmware under test.
def stub(addr, callback=None):
    cpu.mem_write(addr,b"\x70\x47")
    if callback is not None:
        cpu.hook_add(UC_HOOK_CODE,lambda a,b,c,d: callback(),begin=addr,end=addr)

def r(i):
    return cpu.reg_read(REGS[i])

def set_r(i,v):
    cpu.reg_write(REGS[i],v)

events=[]
dma_busy=False
ack_out_nak=True
stop_ack=False
ep_disabled=False

stub(0x0307eeae, lambda: events.append(("ack",)))
stub(0x0307eb72, lambda: events.append(("send",read(r(1),r(0)))))
def observe_control_transfer(*args):
    if read(r(0)+12,1)==b"\x01":
        events.append(("send_desc",half(r(0))))
cpu.hook_add(UC_HOOK_CODE,observe_control_transfer,begin=0x0307ee00,end=0x0307ee00)
stub(0x03080382, lambda: events.append(("stop_dma",)))
stub(0x03055ebc, lambda: set_r(0, int(dma_busy)))
for address in (0x030611fa, 0x0306ec74, 0x030610ce):
    stub(address)
def dma():
    count=r(3)
    events.append(("dma",r(0),r(1),r(2),count,read(r(1),count*4)))
stub(0x03060220,dma)
stub(0x030610d2,lambda: events.append(("dac_rate",r(0))))
stub(0x030560a8,lambda: events.append(("i2s_rate",r(3),word(cpu.reg_read(UC_ARM_REG_SP)+4))))

def controller_write(uc,kind,addr,size,value,ignored):
    global ep_disabled
    if addr==USB+0x804 and value & 0x200:
        if ack_out_nak:
            put(USB+0x14,0x80)
    if addr==USB+0x804 and value & 0x400:
        put(USB+0x14,0)
    if addr==CTRL:
        if value&0x40000000 and value&0x08000000:
            put(INT,2)
            ep_disabled=True
        elif value&0x80000000:
            ep_disabled=False
cpu.hook_add(UC_HOOK_MEM_WRITE,controller_write,begin=USB+0x804,end=CTRL)
def controller_read(uc,kind,addr,size,value,ignored):
    if ep_disabled:
        put(CTRL,word(CTRL)&~0xc0000000)
cpu.hook_add(UC_HOOK_MEM_READ,controller_read,begin=CTRL,end=CTRL)

packet=b""
cursor=0
def fifo(uc,kind,addr,size,value,ignored):
    global cursor
    assert cursor+4<=len(packet)
    uc.mem_write(FIFO,packet[cursor:cursor+4])
    cursor+=4
cpu.hook_add(UC_HOOK_MEM_READ,fifo,begin=FIFO,end=FIFO)
def rx(payload,expected_size):
    global packet,cursor
    packet=payload+bytes(-len(payload)%4)
    cursor=0
    execute(word(USB_CLASS+0x28)&~1,2,1,len(payload),USB_CLASS)
    assert cursor==len(packet)
    if expected_size is not None:
        assert word(SIZE)==0x80000+expected_size
def setup(bm,cmd,value=0,index=0,length=0):
    cpu.mem_write(REQUEST_BUF,pack("<BBHHH",bm,cmd,value,index,length))
    return execute(word(USB_CLASS+0x18)&~1,REQUEST_BUF,USB_CLASS)
def poll():
    execute(0x030801fc)
def current():
    return (word(STREAM)&255,word(STREAM+4)&255)
def wire(values,bits):
    return b"".join(x.to_bytes(bits//8,"little",signed=True) for x in values)
def converted(values,bits):
    return pack("<"+"i"*len(values),*(x<<(32-bits) for x in values))

execute(0x0307f216)
execute(0x0307fd00)
put(USB+0x24,147,0x00200093)
put(USB+0x48,0,308<<16)
EP0=0x03024934
CFG=word(EP0+0x24)
assert CFG==USB_CLASS
hs_config=word(CFG+0x10)
fs_config=word(CFG+0x14)
def set_speed(high_speed):
    put(USB+0x808,0 if high_speed else 2)
    execute(0x0307f2dc,EP0)
    assert read(CFG,1)==bytes([3 if high_speed else 2])
def request(bm,cmd,value=0,index=0,length=0):
    cpu.mem_write(EP0+0x1c,pack("<BBHHH",bm,cmd,value,index,length))
    execute(0x0307ef60,EP0)
def response(expected):
    assert events[-1]==("send",expected), events[-1]
for hs_speed in (False,True):
    set_speed(hs_speed)
    request(0x80,6,0x100,0,18)
    dev=events[-1][1]
    assert dev[:4]==b"\x12\x01\x00\x02"
    assert dev[4:8]==(b"\xef\x02\x01\x40" if hs_speed else b"\0\0\0\x40")
    assert dev[8:14]==pack("<3H",0x071b,0x320a,revision)
    request(0x80,6,0x600,0,10)
    other=b"\0\0\0\x40" if hs_speed else b"\xef\x02\x01\x40"
    response(b"\x0a\x06\x00\x02"+other+b"\x01\x00")
    for dtype,ptr in ((2,hs_config if hs_speed else fs_config),(7,fs_config if hs_speed else hs_config)):
        desc=bytearray(read(ptr,half(ptr+2)))
        hs_length = 336 if pcm_32 else 290 if rate_192 else 198
        if host_volume:
            hs_length += 18
        assert len(desc)==(hs_length if ptr==hs_config else 230)
        for max_len in (9,512):
            request(0x80,6,dtype<<8,0,max_len)
            n=min(max_len,len(desc))
            assert events[-2]==("send_desc",n)
            desc[1]=dtype
            assert read(0x0302495c,n)==desc[:n]
            response(bytes(desc[:min(n,64)]))
            for p in range(64,n,64):
                execute(0x0307f250,EP0,4)
                response(bytes(desc[p:min(n,p+64)]))
            if n>64:
                execute(0x0307f250,EP0,4)
    request(0,9,1)
    assert read(STATE+2,1)==b"\x01" and events[-1]==("ack",)
    request(1,11,1,1)
    expected_mps=196 if hs_speed else 192
    assert current()[0]==1 and word(CTRL)&0x7ff==expected_mps
    assert word(SIZE)==0x80000+expected_mps
    if hs_speed:
        assert word(CTRL)&0x80000000==0
        put(USB+0x808,0x100)
        execute(0x0307f710,USB+0x840,4,0x10,2,stop=0x0307f72e)
        assert word(CTRL)&0xa0000000==0xa0000000
    request(1,11,0,1)
    assert current()[0]==0
    poll()
    assert current()==(0,0)
    request(0,9,0)
print("Real USB firmware selected separate HS/FS configuration, opposite-speed and device qualifiers at each negotiated speed.",flush=True)

set_speed(True)
request(0,9,1)
request(0xa1,1,0x0200,0x0400,1)
response(b"\x01")
request(0xa1,1,0x0100,0x0400,4)
response(pack("<I",48000))
request(0xa1,2,0x0100,0x0400,2)
clock_rates=(48000,96000,192000) if rate_192 else (48000,96000)
response(pack("<H",len(clock_rates)))
clock_range=pack("<H",len(clock_rates))+b"".join(pack("<3I",rate,rate,0) for rate in clock_rates)
request(0xa1,2,0x0100,0x0400,len(clock_range))
response(clock_range)

def clock_rate(frequency):
    request(0x21,1,0x0100,0x0400,4)
    assert read(EP0,1)==b"\x03"
    assert half(0x03024940)==4
    epbuf=word(0x03024944)
    put(epbuf,frequency)
    cpu.mem_write(0x03024942,pack("<H",4))
    execute(0x0307f250,EP0,2)
    assert events[-1]==("ack",)
    assert read(EP0,1)==b"\x01"

copied=[]
stub(0x030602f6,lambda: copied.append((r(1),read(r(1),r(3)*4))))
if pcm_32:
    descriptors = []
    p = hs_config + 9
    while p < hs_config + half(hs_config+2):
        length = read(p,1)[0]
        assert length >= 2
        descriptors.append(read(p,length))
        p += length
    alt = next(i for i, d in enumerate(descriptors) if d[:5] == b"\x09\x04\x01\x05\x01")
    assert descriptors[alt+2] == bytes((6,0x24,2,1,4,32))
    assert descriptors[alt+3] == pack("<BBBBHB",7,5,2,9,392,2)
    if host_volume:
        feature = next(d for d in descriptors if d[1:4] == b"\x24\x06\x03")
        assert feature == pack("<5B3IB",18,0x24,6,3,1,15,0,0,0)
        output = next(d for d in descriptors if d[1:4] == b"\x24\x03\x02")
        assert output[7] == feature[3]
        control = next(d for d in descriptors if d[:3] == b"\x09\x24\x01")
        assert unpack_from("<H",control,6)[0] == 9+8+17+18+12
for rate in clock_rates:
    clock_rate(rate)
    request(0xa1,1,0x0100,0x0400,4)
    response(pack("<I",rate))
    alternates=((3,16,388,3),(4,24,300,2)) if rate==192000 else ((1,16,196,3),(2,24,294,3))
    if pcm_32:
        alternates += ((5,32,392,2),)
    for host_alt,bits,mps,interval in alternates:
        request(1,11,host_alt,1)
        mode=7+clock_rates.index(rate) if bits==32 else 2*clock_rates.index(rate)+(1 if bits==16 else 2)
        assert current()[0]==mode and word(CTRL)&0x7ff==mps
        request(0x81,10,0,1,1)
        response(bytes([host_alt]))
        poll()
        execute(0x0307ff56)
        assert word(MUSIC+8)==rate and word(MUSIC+0x9c)==bits
        assert word(MUSIC+12)==rate*8
        block_size=4096 if rate==192000 else 2048
        assert word(STATE+0x40)==block_size//4
        put(STATE+0x44,rate*2)
        assert execute(0x03080358)==1000
        execute(0x03080088,stop=0x030800ac)
        assert events[-1]==("i2s_rate",rate,0x17)
        assert ("dac_rate",rate) in events[-3:]
        gold=[]
        put(USB+0x808,0)
        execute(0x0307f710,USB+0x840,4,0x10,2,stop=0x0307f72e)
        transactions=44 if rate==192000 else 41
        for frame in range(transactions):
            put(USB+0x808,((frame*(1<<(interval-1)))&0x3fff)<<8)
            peak=(1<<(bits-1))
            count=rate//(8000>>(interval-1))
            samples=[(123*frame-637*i+peak//2)%peak*2-peak for i in range(count*2)]
            if frame&1:
                samples[-1]=-1
            gold+=samples
            rx(wire(samples,bits),mps)
            assert word(CTRL)&0x30000000==0x10000000
            cpu.reg_write(UC_ARM_REG_FP,0x03024b5c)
            execute(0x0307f6f8,USB+0x840,stop=0x0307f710)
            assert word(SIZE)==0x80000+mps and word(CTRL)>>31
        expected=converted(gold,bits)
        assert read(RING,len(expected))==expected and word(STATE+0x14)==len(expected)
        assert rate<=48000 or len(expected)>=2048
        print(f"HS core: {bits}/{rate}, {transactions} consecutive transfers, PCM alignment and DMA format passed.",flush=True)
        if rate>=96000 and bits>=24:
            put(STATE+0x24,block_size//4)
            put(STATE+0x3c,0)
            cpu.mem_write(0x03024b60,b"\x3c\0")
            cpu.mem_write(0x0302235e,b"\x01")
            cpu.reg_write(UC_ARM_REG_R5,STATE)
            execute(0x03080116,stop=0x0308011a)
            assert read(DMA_BUFFER,block_size)==expected[:block_size]
            assert read(DMA_BUFFER+DMA_HALF_SIZE,block_size)==bytes(block_size)
            cpu.mem_write(STATE+6,b"\0\0")
            copied.clear()
            execute(0x0307fede)
            assert copied==[(DMA_BUFFER,expected[:block_size])]
            assert read(DMA_BUFFER+DMA_HALF_SIZE,block_size)==expected[block_size:2*block_size]
        request(1,11,0,1)
        poll()
        assert current()==(0,0)
request(1,11,6 if pcm_32 else 5 if rate_192 else 3,1)
assert current()==(0,0) and word(USB+0x900)&0x200000
print("HS DMA retained low sample bits, only valid host alternate settings accepted.",flush=True)

request(0x80,6,0x0304,0x0409,62)
raw=events[-1][1]
assert len(raw)==62 and raw[:2]==b"\x3e\x03"
vals=[]
for p in range(2,len(raw),6):
    digits=unpack_from("<3H",raw,p)
    assert all(x&0xf000==0xe000 for x in digits)
    vals.append((digits[0]&4095)|((digits[1]&4095)<<12)|((digits[2]&4095)<<24))
assert vals[0:2]==[clock_rates[-1],32 if pcm_32 else 24] and vals[2:4]==[0,0]
mask=0
for sample in unpack(f"<{block_size//4}I",expected[block_size:2*block_size]):
    mask|=sample
assert vals[7]==mask and vals[5:7]==[0,0]
print("Fixed diagnostic details returned all ten fields as bounded UTF-16 data.",flush=True)

# Stop the old DMA without waiting in the audio task.
clock_rate(96000)
request(1,11,1,1)
poll()
execute(0x0307ff56)
put(STATE+0x2c,1,1)
dma_busy=True
request(1,11,2,1)
ring_before=word(STATE+0x14)
poll()
assert current()==(4,3) and word(STATE+0x14)==ring_before
assert half(STATE+6)==4 and half(STATE+8)==3
assert ("stop_dma",) not in events
rx(bytes(288),294)
assert word(STATE+0x14)==ring_before
dma_busy=False
stub(0x0306656e)
poll()
assert current()==(4,4) and word(STATE+0x14)==word(STATE+0x18)==0
# Monitor a short ring while DMA is active, then resume after it stops.
put(STATE,1)
put(STATE+0x2c,1,1)
put(STATE+0x14,0x800)
put(STATE+0x18,0)
dma_busy=True
execute(0x03080460)
assert read(STATE,1)==b"\x02" and half(STATE+6)==4
assert half(STATE+8)==3
execute(0x03080460)
dma_busy=False
put(STATE+0x14,0x7000)
stub(0x030803f0,lambda:events.append(("resume",)))
execute(0x03080460)
assert events[-1]==("resume",) and read(STATE,1)==b"\x01"
print("DMA switch and underrun monitoring returned immediately while the channel was active.",flush=True)

# Check the fast reader against the original byte semantics across wrap.
put(STREAM,0,0)
def ring_data(start,data):
    a=min(len(data),0x9000-start)
    cpu.mem_write(RING+start,data[:a])
    if a<len(data):cpu.mem_write(RING,data[a:])
for start in (0,8,0x8ff8,0x8fe0):
    for available in (0,8,16,2040,2048,2056,4096):
        for requested in (0,8,16,2048):
            payload=bytes((i*17+39)&255 for i in range(available))
            ring_data(start,payload)
            put(STATE+0x14,(start+available)%0x9000)
            put(STATE+0x18,start,0)
            cpu.mem_write(DMA_BUFFER,bytes([0xac])*4096)
            got=execute(0x0307fe2e,DMA_BUFFER,requested)
            count=min(available,requested)
            assert got==count
            assert read(DMA_BUFFER,count)==payload[:count]
            assert read(DMA_BUFFER+count,4096-count)==bytes([0xac])*(4096-count)
            assert word(STATE+0x18)==(start+count)%0x9000
            assert word(STATE+0x1c)==count
for requested in (1,7,19,2047):
    payload=bytes((i*29+9)&255 for i in range(2056))
    ring_data(0x8ff8,payload)
    put(STATE+0x14,(0x8ff8+len(payload))%0x9000)
    put(STATE+0x18,0x8ff8,0)
    got=execute(0x0307fe2e,DMA_BUFFER+1,requested)
    assert got==requested and read(DMA_BUFFER+1,requested)==payload[:requested]
print("Fast ring reader preserved full, short, zero, unaligned and wrapped input behavior.",flush=True)
# Legacy interrupt bits acknowledge without entering the audio extension.
put(CTRL,0x1234)
execute(0x0307f710,USB+0x840,4,0xe,2,stop=0x0307f72e)
assert word(INT)==0xe and word(CTRL)==0x1234
print("Shared USB handler preserved legacy endpoint error acknowledgements.",flush=True)

if rate_192:
    # Exercise both orders of clock and interface requests. At 192 kHz a
    # lower-bandwidth alternate stays selected while its PCM path is idle.
    request(0,9,1)
    poll()
    clock_rate(192000)
    request(1,11,2,1)
    assert current()==(0,0) and (word(CTRL)&0x7ff)==294
    request(0x81,10,0,1,1)
    response(b"\x02")
    request(1,11,4,1)
    assert current()[0]==6 and (word(CTRL)&0x7ff)==300
    poll()
    assert current()==(6,6)
    clock_rate(48000)
    assert current()[0]==2 and (word(CTRL)&0x7ff)==300
    request(0x81,10,0,1,1)
    response(b"\x04")
    clock_rate(192000)
    assert current()[0]==6 and (word(CTRL)&0x7ff)==300
    request(1,11,0,1)
    poll()

    if pcm_32:
        request(1,11,5,1)
        assert current()[0]==9 and (word(CTRL)&0x7ff)==392
        for rate,mode in ((48000,7),(96000,8),(192000,9)):
            clock_rate(rate)
            poll()
            execute(0x0307ff56)
            assert current()==(mode,mode)
            assert word(MUSIC+8)==rate and word(MUSIC+0x9c)==32
        request(1,11,0,1)
        poll()

    # Run the stock startup path as well as the DMA callback tested above.
    request(1,11,4,1)
    poll()
    execute(0x0307ff56)
    if revision >= 0x0209:
        # An odd 49-frame packet crosses the ring end after its first frame.
        start = 0x8ff8
        samples = [(-1)**i * (0x1234ff - 0x101 * i) for i in range(98)]
        payload = wire(samples, 24)
        expected = converted(samples, 24)
        put(STATE+0x14, start, start)
        rx(payload, 300)
        end = len(expected) - 8
        assert word(STATE+0x14) == end
        assert read(RING+start, 8) + read(RING, end) == expected
        assert read(0x0302ed88, len(payload)) == payload

        # A full ring preserves the data already queued.
        put(STATE+0x18, end+8)
        previous = read(RING, 0x9000)
        dropped = word(STREAM+32)
        rx(payload, 300)
        assert read(RING, 0x9000) == previous
        assert word(STATE+0x14) == end and word(STREAM+32) == dropped+1
        put(STATE+0x14, 0, 0)
    data=pack("<1024I",*(0x11223300+i*0x100 for i in range(1024)))
    ring_data(0,data*2)
    put(STATE+0x14,len(data)*2)
    cpu.reg_write(UC_ARM_REG_R6,0)
    execute(0x030800d2,stop=0x03080132)
    assert events[-1]==("dma",5,DMA_BUFFER+4096,0x40030024,1024,bytes(4096))
    assert read(DMA_BUFFER,4096)==data

    # Use the stock divider and DAC rate selection with modelled MMIO.
    resident_offset=0x297cc
    for address,length in ((0x030610d2,2),(0x030560a8,2)):
        start=resident_offset+address-0x03050000
        cpu.mem_write(address,image[start:start+length])
    for address in (0x03052688,0x030527d4,0x03052a10,0x0305272e,0x03052154,0x03052128,0x030526f8):
        stub(address)
    pll_selections=[]
    dac_writes=[]
    stub(0x030515ee,lambda: pll_selections.append((r(0),r(1),r(2))))
    stub(0x03060f00,lambda: dac_writes.append((r(0),r(1))))
    cpu.ctl_flush_tb()
    execute(0x03080088,stop=0x030800ac)
    assert word(0x40030000)==0x401f and word(0x40030008)==0x00013f3f
    assert pll_selections[-1]==(4,7,2) and (0x1000b,6) in dac_writes
    print("192 kHz clock control, stock I2S divider, DAC rate register and 4 KiB DMA startup passed.",flush=True)

    request(1,11,0,1)
    poll()
    set_speed(False)
    request(0,9,1)
    for alternate,bits,rate,packet_size in ((1,16,48000,192),(2,24,48000,288),(3,16,96000,384)):
        request(1,11,alternate,1)
        poll()
        execute(0x0307ff56)
        assert current()==(alternate,alternate)
        assert word(MUSIC+8)==rate and word(MUSIC+0x9c)==bits
        assert word(CTRL)&0x7ff==packet_size
        samples=[-1,1]*(rate//1000)
        rx(wire(samples,bits),packet_size)
        values=converted(samples,bits)
        assert read(RING,len(values))==values
        request(1,11,0,1)
        poll()
    request(1,11,4,1)
    assert word(USB+0x900)&0x200000 and current()==(0,0)
    print("All three full-speed formats passed; full-speed alternate 4 stalled.",flush=True)

if pcm_32 and not host_volume:
    for bits in (16,24,32):
        peak = 1 << (bits-1)
        samples = [-peak, -peak+1, -513, -61, -2, -1, 0, 1, 2, 61, 513, peak-2, peak-1]
        # Legacy 16-bit input can gain fractional bits in the existing
        # left-aligned 24-bit volume path.
        width = max(24,bits)
        input_words = [x << (width-bits) for x in samples]
        put(MUSIC+0x9c,bits)
        put(STATE+0x24,len(samples))
        for gain,mute,output in [(g,0,1) for g in range(61)] + [(255,0,1),(60,1,1),(60,0,0)]:
            cpu.mem_write(0x03024b60,bytes((gain,mute)))
            cpu.mem_write(0x0302235e,bytes((output,)))
            cpu.mem_write(DMA_BUFFER,converted(samples,bits))
            execute(0x0307fd7c,DMA_BUFFER,STATE+0x24)
            scale = min(gain,60) if output and not mute else 0
            scaled = [(abs(x)*scale//60)*(-1 if x<0 else 1) for x in input_words]
            assert read(DMA_BUFFER,len(samples)*4) == converted(scaled,width), (bits,gain,mute,output)
            assert word(STATE+0x34) == scaled.count(0)
            assert word(STATE+0x38) == int(len(scaled)-scaled.count(0)>=2)
    print("16-, 24- and 32-bit gain, signed endpoints, low bits and mute passed.",flush=True)

if pcm_32:
    # Exercise the real enumeration callback and the stock physical key
    # handler. Only the two DAC register writes are modelled.
    for hs in (False,True):
        cpu.mem_write(STATE+4,b"\0")
        set_speed(hs)
        enabled = not hs or revision >= 0x0211
        assert read(STATE+4,1) == bytes((int(enabled),))
        for before,key,after,attenuation in (
            (60,0x10000020,61,59),
            (60,0x10000010,59,61),
            (1,0x10000010,0,255),
            (0,0x10000020,1,200),
            (120,0x10000020,120,None),
            (0,0x10000010,0,None),
        ):
            cpu.mem_write(0x0302235e,bytes((before,)))
            dac_writes.clear()
            execute(0x030802ce,key)
            assert read(0x0302235e,1) == bytes((after if enabled else before,))
            expected = [(0x90001,attenuation),(0x90002,attenuation)] if enabled and attenuation is not None else []
            assert dac_writes == expected
    print("USB-speed connection state and the native volume keys matched the expected DAC writes.",flush=True)

if host_volume:
    FEATURE = 0x0300
    MUTE = 0x0100
    VOLUME = 0x0200
    GAIN_ONE = 1 << 30
    def decibels(db):
        return pack("<h", int(db * 256))

    def write_host_control(control, data, received=None):
        request(0x21,1,control,FEATURE,len(data))
        assert read(EP0,1) == b"\x03" and half(EP0+12) == len(data)
        cpu.mem_write(word(EP0+16),data)
        cpu.mem_write(EP0+14,pack("<H",len(data) if received is None else received))
        execute(0x0307f250,EP0,2)
        if received is None or received >= len(data):
            assert events[-1] == ("ack",)

    for hs in (False, True):
        set_speed(hs)
        request(0,9,1)
        get_cur = 1 if hs else 0x81
        request(0xa1,get_cur,VOLUME,FEATURE,2)
        response(decibels(0))
        request(0xa1,get_cur,MUTE,FEATURE,1)
        response(b"\0")
        if hs:
            limits = b"\x01\x00" + decibels(-96) + decibels(0) + decibels(1)
            request(0xa1,2,VOLUME,FEATURE,2)
            response(limits[:2])
            request(0xa1,2,VOLUME,FEATURE,8)
            response(limits)
        else:
            for command, level in ((0x82,-96),(0x83,0),(0x84,1)):
                request(0xa1,command,VOLUME,FEATURE,2)
                response(decibels(level))

        for sent, reported in ((-20,-20),(-23.5,-24),(-100,-96),(3,0),(-128,-128)):
            write_host_control(VOLUME,decibels(sent))
            request(0xa1,get_cur,VOLUME,FEATURE,2)
            response(decibels(reported))
            gain = unpack("<i",read(STREAM+64,4))[0] + GAIN_ONE
            assert abs(gain / GAIN_ONE - (0 if reported == -128 else 10**(reported/20))) < 1e-8

        write_host_control(VOLUME,decibels(-20))
        write_host_control(VOLUME,decibels(0),received=1)
        write_host_control(VOLUME,decibels(0),received=3)
        request(0xa1,get_cur,VOLUME,FEATURE,2)
        response(decibels(-20))
        for value, expected_mute in ((1,1),(2,1),(0,0)):
            write_host_control(MUTE,bytes([value]))
            request(0xa1,get_cur,MUTE,FEATURE,1)
            response(bytes([expected_mute]))
        for bm, command, control, entity, length in (
            (0xa1, get_cur, VOLUME+1, FEATURE, 2),
            (0xa1, get_cur, VOLUME, FEATURE+1, 2),
            (0xa1, 2 if hs else 0x82, MUTE, FEATURE, 1),
            (0x21, 1, VOLUME, FEATURE, 1),
            (0x21, 1, VOLUME, FEATURE, 0),
        ):
            endpoint = USB + (0x900 if bm & 0x80 or not length else 0xb00)
            put(endpoint,word(endpoint)&~0x200000)
            request(bm,command,control,entity,length)
            assert word(endpoint)&0x200000, (hs,bm,command,control,entity,length)
        request(0x21,0x0a,0,2,0)
        assert events[-1] == ("ack",)
        set_speed(hs)
        request(0xa1,get_cur,VOLUME,FEATURE,2)
        response(decibels(0))
    print("UAC1 and UAC2 host volume/mute, limits, silence, invalid requests and reconnect reset passed.",flush=True)

    # Exercise the stock 48 kHz restart with a full audio ring. Earlier
    # monitor checks stubbed its entry point, so restore the first halfword.
    resume = 0x030803f0
    offset = load-base+0x200+resume-run_address
    cpu.mem_write(resume,image[offset:offset+2])
    stub(0x0305094e)
    stub(0x03066ba0)
    cpu.ctl_flush_tb()
    for hs, rate, bits, alternate in ((False,48000,16,1),(True,48000,16,1),
                                      (True,96000,24,2),(True,192000,32,5)):
        set_speed(hs)
        request(0,9,1)
        write_host_control(VOLUME,decibels(-20))
        if hs:
            clock_rate(rate)
        request(1,11,alternate,1)
        poll()
        execute(0x0307ff56)
        request(0xa1,1 if hs else 0x81,VOLUME,FEATURE,2)
        response(decibels(-20))
        count = word(STATE+0x40)
        peak = 1 << (bits-1)
        samples = ([-peak,-peak+1,-513,-61,-2,-1,0,1,2,61,513,peak-1] * count)[:count]
        width = max(24,bits)
        for db, mute, local_enabled in ((0,0,1),(-20,0,1),(-96,0,1),(-128,0,1),
                                        (-20,1,1),(-20,0,0),(-20,0,1)):
            write_host_control(VOLUME,decibels(db))
            write_host_control(MUTE,bytes([mute]))
            cpu.mem_write(0x0302235e,bytes([local_enabled]))
            gain = unpack("<i",read(STREAM+64,4))[0] + GAIN_ONE
            if mute or not local_enabled:
                gain = 0
            scaled = [(abs(x) * gain * (1 << (width-bits)) // GAIN_ONE) * (-1 if x < 0 else 1)
                      for x in samples]
            gold = converted(scaled,width)
            ring_data(0,converted(samples,bits)*2)
            put(STATE+0x14,count*8,0)
            put(STATE+0x20,0,count)
            put(STATE+0x3c,0)
            cpu.mem_write(STATE+6,bytes(4))
            if rate >= 96000:
                cpu.reg_write(UC_ARM_REG_R5,STATE)
                execute(0x03080116,stop=0x0308011a)
                copied.clear()
            execute(0x0307fede)
            actual = copied[-1][1] if rate >= 96000 else events[-1][-1]
            assert actual == gold, (hs,rate,bits,db,mute,local_enabled)
            assert word(STATE+0x34) == scaled.count(0)
            assert word(STATE+0x38) == int(count-scaled.count(0)>=2)
        if rate == 48000:
            put(STATE+0x14,count*8,0)
            cpu.mem_write(STATE+6,pack("<HH",4,3))
            execute(resume)
            assert events[-1][-1] == bytes(count*4)
            execute(0x0307fede)
            assert events[-1][-1] == gold
        request(1,11,0,1)
        poll()
    print("Host attenuation and mute reached real DMA paths at 48, 96 and 192 kHz, including 48 kHz underrun restart.",flush=True)

