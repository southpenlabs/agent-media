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
def epiano(f,d,dec=1.6,bright=.25):
    n=int(R*d); return [min(1,i/(R*.008))*math.exp(-dec*i/R)*(math.sin(2*math.pi*f*i/R)+bright*math.sin(2*math.pi*2*f*i/R)*math.exp(-3*i/R)) for i in range(n)]
def padn(freqs,d,wob=.3):
    n=int(R*d); return [math.sin(math.pi*i/n)**1.5*sum(math.sin(2*math.pi*f*i/R+wob*math.sin(2*math.pi*.4*i/R+k)) for k,f in enumerate(freqs))/len(freqs) for i in range(n)]
def hz(m): return 440*2**((m-69)/12)
def rain(d):
    random.seed(11); y=z=0; out=[]
    for i in range(int(R*d)):
        y+=.08*(random.uniform(-1,1)-y); z+=.02*(y-z); out.append(y-z)
    return out
P={
 "blend":   ([(57,[57,60,64,67]),(53,[53,57,60,64]),(50,[50,53,57,60]),(55,[55,58,62,65])],4.4,4,.25,.3),
 "morning": ([(60,[60,64,67,71]),(65,[65,69,72,76]),(57,[57,60,64,67]),(67,[67,71,74,77])],3.6,4,.4,.2),
 "rainy":   ([(57,[57,60,64,67]),(53,[53,57,60,64]),(57,[57,60,64,67]),(52,[52,55,59,62])],4.8,3,.2,.35),
 "night":   ([(50,[50,57,60,64]),(46,[46,53,57,62]),(43,[43,50,53,57]),(45,[45,52,55,60])],5.6,2,.12,.4),
}
mood=sys.argv[1]; prog,bar,notes,bright,wob=P[mood]; random.seed(7)
s=[]; t=0
for rep in range(2):
    for root,ch in prog:
        mix(s,padn([hz(m) for m in ch],bar+0.8,wob),t,0.5)
        mix(s,epiano(hz(root-12),bar,0.9,bright),t,0.35)
        for j in range(notes):
            mix(s,epiano(hz(random.choice(ch)+12),1.8,1.4,bright),t+j*bar/notes+random.uniform(0,.12),0.3)
        t+=bar
if mood=="rainy": mix(s,rain(t+1),0,0.6)
save(f"bed-{mood}.wav",s,-24)
