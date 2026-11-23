#!/usr/bin/env python3
"""Regenerate icons/grayjay-link.svg from Grayjay's app icon.

Usage: tools/make_icon.py <app.grayjay.Grayjay.svg> [white|blue] > plasmoid/contents/icons/grayjay-link.svg (then copy to firefox/icons/)
"""
import math, sys, re
base = open(sys.argv[1]).read()
inner = re.search(r'<svg[^>]*>(.*)</svg>', base, re.S).group(1)
defs = re.search(r'<defs>.*?</defs>', inner, re.S).group(0)
body = inner.replace(defs, '').strip()
cx, cy, R = 90.0, 90.0, 24.0
r, sw = 12.5, 4.2
def pt(a): a=math.radians(a); return (cx + r*math.cos(a), cy + r*math.sin(a))
def arrow(a_end):
    # tangent for clockwise travel at angle a_end
    a=math.radians(a_end); d=(-math.sin(a), math.cos(a)); n=(-d[1], d[0]); P=pt(a_end)
    tip=(P[0]+d[0]*6.2, P[1]+d[1]*6.2); b1=(P[0]+n[0]*5.6-d[0]*0.6, P[1]+n[1]*5.6-d[1]*0.6); b2=(P[0]-n[0]*5.6-d[0]*0.6, P[1]-n[1]*5.6-d[1]*0.6)
    return "M%.2f %.2fL%.2f %.2fL%.2f %.2fZ" % (*tip, *b1, *b2)
def arc(a0, a1):
    p0, p1 = pt(a0), pt(a1)
    return "M%.2f %.2fA%.2f %.2f 0 0 1 %.2f %.2f" % (*p0, r, r, *p1)
fill = sys.argv[2] if len(sys.argv) > 2 else "white"
fg = "#0182E7" if fill == "white" else "white"
bfill = 'fill="white"' if fill=="white" else 'fill="url(#badge_gradient)"'
svg = f'''<svg width="128" height="128" viewBox="0 0 128 128" fill="none" xmlns="http://www.w3.org/2000/svg">
<!-- Base: Grayjay app icon (c) FUTO Holdings, Inc. Badge: grayjay-link. -->
{body}
<circle cx="{cx}" cy="{cy}" r="{R+2.6}" fill="#121212"/>
<circle cx="{cx}" cy="{cy}" r="{R}" {bfill}/>
<path d="{arc(200, 330)}" stroke="{fg}" stroke-width="{sw}" stroke-linecap="round"/>
<path d="{arrow(330)}" fill="{fg}"/>
<path d="{arc(20, 150)}" stroke="{fg}" stroke-width="{sw}" stroke-linecap="round"/>
<path d="{arrow(150)}" fill="{fg}"/>
{defs.replace('</defs>', f'<linearGradient id="badge_gradient" x1="{cx}" y1="{cy+R}" x2="{cx}" y2="{cy-R}" gradientUnits="userSpaceOnUse"><stop stop-color="#01D6E6"/><stop offset="1" stop-color="#0182E7"/></linearGradient></defs>')}
</svg>
'''
print(svg)
