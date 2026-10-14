"""Normalize receiver outputs without treating GPS or serial as CRC evidence."""
import math,re
NUMBER=r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?'
GPS=re.compile(r'\bdrone_GPS\s*=\s*('+NUMBER+r')\s*,\s*('+NUMBER+r')')
def coordinate(lat,lon):
    try:
        lat,lon=float(lat),float(lon)
        if math.isfinite(lat) and math.isfinite(lon) and -90<=lat<=90 and -180<=lon<=180 and (lat!=0 or lon!=0):return [lat,lon]
    except (TypeError,ValueError):pass
    return None

def normalize(source,row,uart_order='latlon'):
    if source=='e200':
        p=row['decoded'];return dict(source=source,t=row['monotonic_ns']/1e9,serial=p['serial_number'],model=p.get('device_type'),crc='unknown',position=coordinate(p.get('drone_lat'),p.get('drone_lon')),frequency=p.get('freq'),altitude=p.get('geodetic_altitude'))
    if source=='uart':
        flag=row.get('crc_flag');return dict(source=source,t=row['monotonic_ns']/1e9,serial=row['serial'],model=row.get('model'),crc='reported_pass' if flag==1 else 'reported_fail' if flag==0 else 'unknown',position=coordinate(row.get('latitude'),row.get('longitude')),frequency=row.get('frequency_mhz'),altitude=row.get('altitude'),port=row.get('port'))
    r=row['result']
    if not r.get('crc_valid'):return None
    p=r['decode_result'].get('payload') or {};return dict(source='ours',t=row['completed_monotonic_s'],serial=p.get('serial_number','unknown'),model=p.get('device_type'),crc='verified_pass',position=coordinate(p.get('latitude'),p.get('longitude')),frequency=None,altitude=p.get('altitude'),sequence=p.get('sequence_number'))

def add_uart_coordinates(row,line,order):
    m=GPS.search(line)
    if m:
        a,b=map(float,m.groups());row.update(latitude=a if order=='latlon' else b,longitude=b if order=='latlon' else a)
    alt=re.search(r'\bAltitude\s*=\s*('+NUMBER+r')',line)
    if alt:row['altitude']=float(alt[1])
    return row
