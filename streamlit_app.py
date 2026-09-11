"""Streamlit dashboard for frozen 09:20 option money leaders."""
import os
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import plotly.graph_objects as go
import psycopg
import streamlit as st
from psycopg.rows import dict_row

IST=ZoneInfo("Asia/Kolkata"); DB=os.getenv("NEON_DATABASE_URL") or os.getenv("DATABASE_URL")
st.set_page_config(page_title="Option AVWAP",page_icon="📊",layout="wide")
if not DB:st.error("NEON_DATABASE_URL is missing");st.stop()

# Create empty tables when the dashboard is deployed before the collector.
# CREATE TABLE IF NOT EXISTS is safe to run repeatedly and prevents a blank
# database from crashing the dashboard.
SCHEMA_DDL="""
CREATE TABLE IF NOT EXISTS public.option_avwap_universe (
 trading_date date NOT NULL, symbol text NOT NULL, option_key text NOT NULL,
 trading_symbol text NOT NULL, option_type text NOT NULL, strike numeric NOT NULL,
 expiry date NOT NULL, lot_size integer NOT NULL, selection_tag text NOT NULL,
 baseline_volume bigint, volume_0920 bigint, volume_delta bigint,
 baseline_oi bigint, oi_0920 bigint, oi_delta bigint, premium_0920 numeric,
 traded_money_cr numeric, fresh_oi_money_cr numeric, selected_at timestamptz NOT NULL,
 future_key text, PRIMARY KEY(trading_date,symbol,option_key)
);
CREATE TABLE IF NOT EXISTS public.option_avwap_3m (
 trading_date date NOT NULL, symbol text NOT NULL, option_key text NOT NULL,
 trading_symbol text NOT NULL, selection_tag text NOT NULL, option_type text NOT NULL,
 strike numeric NOT NULL, expiry date NOT NULL, candle_start timestamptz NOT NULL,
 candle_end timestamptz NOT NULL, open numeric, high numeric, low numeric, close numeric,
 volume bigint, oi bigint, avwap_high numeric, avwap_low numeric,
 hourly_avwap_high numeric, hourly_avwap_low numeric, high_cross text, low_cross text,
 future_price numeric, updated_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(trading_date,option_key,candle_start)
);
CREATE TABLE IF NOT EXISTS public.option_avwap_heartbeat (
 service_name text PRIMARY KEY, trading_date date, status text NOT NULL,
 candidates integer DEFAULT 0, selected integer DEFAULT 0,
 last_cycle_at timestamptz, message text,
 updated_at timestamptz NOT NULL DEFAULT now()
);
"""

try:
    with psycopg.connect(DB) as schema_connection:
        schema_connection.execute(SCHEMA_DDL)
        schema_connection.commit()
except Exception as exc:
    st.error(f"Unable to initialise the Option AVWAP tables: {exc}")
    st.stop()

@st.cache_data(ttl=30)
def q(sql,params=()):
    with psycopg.connect(DB,row_factory=dict_row) as c:
        d=pd.DataFrame(c.execute(sql,params).fetchall())
    for col in set(d.columns)&{"selected_at","candle_start","candle_end","last_cycle_at","updated_at","first_cross_time","latest_cross_time"}:
        d[col]=pd.to_datetime(d[col],utc=True,errors="coerce").dt.tz_convert(IST)
    return d

daydf=q("SELECT max(trading_date) AS latest_trading_date FROM public.option_avwap_universe")
if daydf.empty or pd.isna(daydf.iloc[0,0]):st.info("No 09:20 option selection is available yet.");st.stop()
day=daydf.iloc[0,0]
universe=q("SELECT * FROM public.option_avwap_universe WHERE trading_date=%s ORDER BY symbol,selection_tag",(day,))
latest=q("""WITH x AS (SELECT *,row_number() over(partition by option_key order by candle_start desc) rn FROM public.option_avwap_3m WHERE trading_date=%s), e AS (SELECT option_key,min(candle_end) FILTER(WHERE high_cross IS NOT NULL OR low_cross IS NOT NULL) first_cross_time,max(candle_end) FILTER(WHERE high_cross IS NOT NULL OR low_cross IS NOT NULL) latest_cross_time,count(*) FILTER(WHERE high_cross IS NOT NULL OR low_cross IS NOT NULL) cross_count FROM public.option_avwap_3m WHERE trading_date=%s GROUP BY option_key) SELECT x.*,e.first_cross_time,e.latest_cross_time,coalesce(e.cross_count,0) cross_count FROM x LEFT JOIN e USING(option_key) WHERE rn=1 ORDER BY symbol""",(day,day))
events=q("SELECT symbol,trading_symbol,selection_tag,option_type,strike,candle_end crossing_time,close option_price,future_price,avwap_high,hourly_avwap_high,avwap_low,hourly_avwap_low,high_cross,low_cross FROM public.option_avwap_3m WHERE trading_date=%s AND(high_cross IS NOT NULL OR low_cross IS NOT NULL) ORDER BY candle_end DESC",(day,))
hb=q("SELECT * FROM public.option_avwap_heartbeat WHERE service_name='option_avwap_collector'")

st.title("Stock Options — 09:20 Money Leaders AVWAP")
st.caption("ATM ±3 strikes · winners frozen at 09:20 IST · continuing 3-minute AVWAP versus completed 09:15–10:15 hourly levels")
if st.button("Refresh now",type="primary"):st.cache_data.clear();st.rerun()
st.caption(f"Trading date: {day} · Checked: {datetime.now(IST):%d %b %Y %H:%M:%S} IST")
if not hb.empty:
    h=hb.iloc[0]; tick=h.last_cycle_at.strftime("%d %b %Y %H:%M:%S IST") if pd.notna(h.last_cycle_at) else "—"
    st.caption(f"Collector: {h.status} · Candidates: {h.candidates} · Selected contracts: {h.selected} · Last cycle: {tick}")
c1,c2,c3,c4=st.columns(4)
c1.metric("Stocks selected",universe.symbol.nunique());c2.metric("Contracts tracked",universe.option_key.nunique())
c3.metric("Crossing events",len(events));c4.metric("Symbols crossed",events.symbol.nunique() if not events.empty else 0)

tabs=st.tabs(["09:20 selection","Live scanner","Crossing log","Chart"])
with tabs[0]:
    cols=["symbol","selection_tag","option_type","strike","trading_symbol","premium_0920","volume_delta","oi_delta","traded_money_cr","fresh_oi_money_cr"]
    st.dataframe(universe[cols],use_container_width=True,hide_index=True)
with tabs[1]:
    if latest.empty:st.info("Selected-option candles have not been written yet.")
    else:
        v=latest[["symbol","trading_symbol","selection_tag","close","future_price","avwap_high","hourly_avwap_high","avwap_low","hourly_avwap_low","cross_count","first_cross_time","latest_cross_time","candle_end"]].copy()
        for col in ["first_cross_time","latest_cross_time","candle_end"]:v[col]=v[col].dt.strftime("%d %b %Y %H:%M:%S IST").fillna("—")
        st.dataframe(v,use_container_width=True,hide_index=True)
with tabs[2]:
    if events.empty:st.info("No crossing has been recorded.")
    else:
        v=events.copy();v["crossing_time"]=v.crossing_time.dt.strftime("%d %b %Y %H:%M:%S IST")
        st.dataframe(v,use_container_width=True,hide_index=True)
with tabs[3]:
    if latest.empty:st.info("No chart data yet.")
    else:
        label=latest.apply(lambda r:f"{r.symbol} — {r.trading_symbol} [{r.selection_tag}]",axis=1)
        choice=st.selectbox("Selected option",label.tolist()); key=latest.loc[label==choice,"option_key"].iloc[0]
        hist=q("SELECT * FROM public.option_avwap_3m WHERE trading_date=%s AND option_key=%s ORDER BY candle_start",(day,key))
        fig=go.Figure(go.Candlestick(x=hist.candle_start,open=hist.open,high=hist.high,low=hist.low,close=hist.close,name="Option"))
        fig.add_scatter(x=hist.candle_start,y=hist.avwap_high,name="3M AVWAP High");fig.add_scatter(x=hist.candle_start,y=hist.avwap_low,name="3M AVWAP Low")
        hh=hist.hourly_avwap_high.dropna();hl=hist.hourly_avwap_low.dropna()
        if not hh.empty:fig.add_hline(y=float(hh.iloc[-1]),line_dash="dash",annotation_text="1H High")
        if not hl.empty:fig.add_hline(y=float(hl.iloc[-1]),line_dash="dash",annotation_text="1H Low")
        fig.update_layout(height=620,xaxis_rangeslider_visible=False,xaxis_title="Time (IST)")
        st.plotly_chart(fig,use_container_width=True)
