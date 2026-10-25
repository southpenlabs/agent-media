import math, random, wave, array, sys
R=48000
def save(name,s,db):
    pk=max(abs(x) for x in s) or 1; k=10**(db/20)*32767/pk
    with wave.open(name,"wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(R); w.writeframes(array.array("h",(int(x*k) for x in s)).tobytes())
def mix(d,s,at,g=1.0):
    o=int(R*at)
    if len(d)<o+len(s): d.extend([0.0]*(o+len(s)-len(d)))
    for i,x in enumerate(s): d[o+i]+=g*x
def epiano(f,d,dec=1.6):
    n=int(R*d); return [min(1,i/(R*.008))*math.exp(-dec*i/R)*(math.sin(2*math.pi*f*i/R)+.25*math.sin(2*math.pi*2*f*i/R)*math.exp(-3*i/R)) for i in range(n)]
def padn(freqs,d):
    n=int(R*d); return [math.sin(math.pi*i/n)**1.5*sum(math.sin(2*math.pi*f*i/R+.3*math.sin(2*math.pi*.4*i/R+k)) for k,f in enumerate(freqs))/len(freqs) for i in range(n)]
def hz(m): return 440*2**((m-69)/12)
mood=sys.argv[1]; random.seed(5)
if mood=="calm":   prog=[(57,[57,60,64,67]),(53,[53,57,60,64]),(48,[48,52,55,59]),(55,[55,59,62,65])]; bar=4.0  # Am7 Fmaj7 Cmaj7 G7
else:              prog=[(50,[50,53,57,60]),(55,[55,58,62,65]),(48,[48,52,55,59]),(53,[53,57,60,64])]; bar=4.8  # Dm7 Gm7 Cmaj7 Fmaj7, slower
s=[]; t=0
for rep in range(2):
    for root,ch in prog:
        mix(s,padn([hz(m) for m in ch],bar+0.8),t,0.5)
        mix(s,epiano(hz(root-12),bar,0.9),t,0.35)
        for j in range(4 if mood=="calm" else 3):
            m=random.choice(ch)+12; mix(s,epiano(hz(m),1.6),t+j*bar/(4 if mood=="calm" else 3)+random.uniform(0,.1),0.3)
        t+=bar
save(f"bed-{mood}.wav",s,-24)
