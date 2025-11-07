import React, { useEffect, useMemo, useState } from 'react'
import { motion } from 'framer-motion'
import { CircleGauge, CalendarDays, UtensilsCrossed, Plus, Trash2, BrainCircuit, Target, BarChart3, Settings, MessageSquareQuote, Camera } from 'lucide-react'

// @ts-ignore
const tg = (typeof window !== 'undefined' && (window as any).Telegram && (window as any).Telegram.WebApp) || null
const API_BASE = import.meta.env.VITE_API_BASE || '' // если пусто — относительные пути

interface Meal { id: number; time: string; kcal: number; item: string }

function useTelegramTheme(){
  const [cls, setCls] = useState({ bg: 'bg-white', text: 'text-gray-900', muted: 'text-gray-500', card: 'bg-white' })
  useEffect(() => {
    if(!tg) return
    tg.expand?.()
    const p = tg.themeParams || {}
    const root = document.documentElement
    const setVar = (k:string,v?:string)=> v && root.style.setProperty(k,v)
    setVar('--tg-bg', p.bg_color)
    setVar('--tg-text', p.text_color)
    setVar('--tg-hint', p.hint_color)
    setVar('--tg-button', p.button_color)
    setCls({
      bg: 'bg-[color:var(--tg-bg,#ffffff)]',
      text: 'text-[color:var(--tg-text,#111827)]',
      muted: 'text-[color:var(--tg-hint,#6b7280)]',
      card: 'bg-[color:var(--tg-bg,#ffffff)]'
    })
  },[])
  return cls
}

function Ring({ value, max }: { value:number; max:number }){
  const clamped = Math.max(0, Math.min(value, max))
  const pct = max>0? (clamped/max)*100 : 0
  const r=56, C=2*Math.PI*r, dash=(pct/100)*C
  return (
    <svg width={140} height={140}>
      <circle cx={70} cy={70} r={r} fill="none" stroke="currentColor" strokeOpacity={0.1} strokeWidth={12}/>
      <circle cx={70} cy={70} r={r} fill="none" stroke="currentColor" strokeWidth={12} strokeDasharray={`${dash} ${C-dash}`} strokeLinecap="round" transform="rotate(-90 70 70)"/>
    </svg>
  )
}

export default function App(){
  const theme = useTelegramTheme()
  const [goal, setGoal] = useState(2200)
  const [dayTotal, setDayTotal] = useState(0)
  const [remaining, setRemaining] = useState(0)
  const [meals, setMeals] = useState<Meal[]>([])
  const [monthTotal, setMonthTotal] = useState(0)
  const [avgPerDay, setAvgPerDay] = useState(0)
  const [addText, setAddText] = useState('')
  const [addKcal, setAddKcal] = useState('')
  const [aiLoading, setAiLoading] = useState(false)
  const [aiResult, setAiResult] = useState<{items:{name:string;grams:number;kcal:number}[], total_kcal:number} | null>(null)
  const [coachText, setCoachText] = useState('')
  const [plan, setPlan] = useState('trial')
  const [trialLeft, setTrialLeft] = useState<number | null>(null)
  const [uploading, setUploading] = useState(false)

  const ring = useMemo(()=>({ value: dayTotal, max: goal }),[dayTotal, goal])
  const initData = tg?.initData || ''

  function api(path:string, opts:RequestInit={}){
    const url = (API_BASE? API_BASE : '') + path
    const headers: Record<string,string> = { }
    if(!(opts.body instanceof FormData)) headers['Content-Type'] = 'application/json'
    if(initData) headers['X-Telegram-Init-Data'] = initData
    return fetch(url, { ...opts, headers: { ...headers, ...(opts.headers||{}) } })
  }

  useEffect(()=>{ (async()=>{
    const prof = await api('/api/profile').then(r=>r.json()).catch(()=>({goal:2200}))
    setGoal(prof.goal ?? 2200)
    const day = await api('/api/summary?period=day').then(r=>r.json()).catch(()=>({total:0, goal:prof.goal ?? 2200, remaining: prof.goal? prof.goal:2200}))
    setDayTotal(day.total||0); setRemaining(day.remaining??Math.max(0,(prof.goal??2200)-(day.total||0))); setMeals(day.items||[])
    const month = await api('/api/summary?period=month').then(r=>r.json()).catch(()=>({total:0, avgPerDay:0}))
    setMonthTotal(month.total||0); setAvgPerDay(month.avgPerDay||0)
    const sub = await api('/api/subscribe/status').then(r=>r.json()).catch(()=>({plan:'trial'}))
    setPlan(sub.plan); setTrialLeft(sub.trial_days_left ?? null)
  })() },[])

  async function handleAddManual(){
    if(!addKcal) return
    const kcal = parseInt(addKcal,10); if(Number.isNaN(kcal)) return
    const res = await api('/api/addmeal',{ method:'POST', body: JSON.stringify({ calories:kcal, description:addText })})
    if(res.ok){
      const day = await api('/api/summary?period=day').then(r=>r.json())
      setDayTotal(day.total||0); setRemaining(day.remaining??Math.max(0,goal-(day.total||0))); setMeals(day.items||[])
      setAddText(''); setAddKcal('')
    }
  }

  async function handleAiEstimate(){
    if(!addText.trim()) return
    setAiLoading(true); setAiResult(null)
    const res = await api('/api/aiadd',{ method:'POST', body: JSON.stringify({ text:addText })})
    const data = await res.json(); setAiResult(data)
    const day = await api('/api/summary?period=day').then(r=>r.json())
    setDayTotal(day.total||0); setRemaining(day.remaining??Math.max(0,goal-(day.total||0))); setMeals(day.items||[])
    setAddText(''); setAddKcal(''); setAiLoading(false)
  }

  async function deleteMeal(id:number){
    await api(`/api/meal/${id}`,{ method:'DELETE' })
    const day = await api('/api/summary?period=day').then(r=>r.json())
    setDayTotal(day.total||0); setRemaining(day.remaining??Math.max(0,goal-(day.total||0))); setMeals(day.items||[])
  }

  async function fetchCoach(){
    const txt = await api('/api/coach').then(r=>r.text())
    setCoachText(txt)
  }
  async function analyzeDay(){
    const txt = await api('/api/analyze_day').then(r=>r.text())
    setCoachText(txt)
  }

  async function openSubscribe(){
    const res = await api('/api/subscribe/create', { method: 'POST' })
    const data = await res.json()
    // @ts-ignore
    tg?.openInvoice?.(data.invoice_url, (status: string) => {
      // 'paid'|'cancelled'|'failed'
    })
  }

  async function onPickPhoto(e: React.ChangeEvent<HTMLInputElement>, type:'receipt'|'dish'){
    const file = e.target.files?.[0]; if(!file) return
    setUploading(true)
    const fd = new FormData()
    fd.append('type', type)
    fd.append('file', file)
    const res = await api('/api/upload', { method: 'POST', body: fd })
    setUploading(false)
    if(res.ok){
      const day = await api('/api/summary?period=day').then(r=>r.json())
      setDayTotal(day.total||0); setRemaining(day.remaining??Math.max(0,goal-(day.total||0))); setMeals(day.items||[])
    }
    e.currentTarget.value = ''
  }

  return (
    <div className={`min-h-screen ${theme.bg} ${theme.text}`}>
      <div className="max-w-3xl mx-auto p-4 pb-28">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-2xl border border-black/5"><UtensilsCrossed size={22}/></div>
            <div>
              <h1 className="text-xl font-semibold">Калории</h1>
              <p className={`text-sm ${theme.muted}`}>Telegram WebApp</p>
            </div>
          </div>
          <button onClick={()=>tg?.close?.()} className="px-3 py-1.5 rounded-xl border border-black/10 text-sm">Закрыть</button>
        </div>

        <div className="mb-3 flex items-center gap-2 text-sm">
          {plan==='pro' ? (
            <span className="px-2 py-1 rounded-lg border border-black/10">PRO ✅</span>
          ) : (
            <span className="px-2 py-1 rounded-lg border border-black/10">Триал {trialLeft ?? '7'} дн.</span>
          )}
          <button onClick={openSubscribe} className="px-3 py-1.5 rounded-lg border border-black/10">Оформить 599⭐</button>
        </div>

        <div className="grid md:grid-cols-3 gap-3 mb-4">
          <motion.div layout className={`rounded-2xl ${theme.card} border border-black/5 p-4 flex items-center gap-4`}>
            <CircleGauge/>
            <div>
              <div className="text-sm flex items-center gap-2"><Target size={16}/> Цель</div>
              <div className="text-2xl font-semibold">{goal} ккал</div>
              <div className={`text-xs ${theme.muted}`}>Осталось: {Math.max(0,remaining)} ккал</div>
            </div>
          </motion.div>

          <motion.div layout className={`rounded-2xl ${theme.card} border border-black/5 p-4 flex items-center gap-4`}>
            <Ring value={ring.value} max={ring.max}/>
            <div>
              <div className="text-sm flex items-center gap-2"><CalendarDays size={16}/> Сегодня</div>
              <div className="text-2xl font-semibold">{dayTotal} ккал</div>
              <div className={`text-xs ${theme.muted}`}>Прогресс: {ring.max? Math.round((ring.value/ring.max)*100):0}%</div>
            </div>
          </motion.div>

          <motion.div layout className={`rounded-2xl ${theme.card} border border-black/5 p-4 flex items-center gap-4`}>
            <BarChart3/>
            <div>
              <div className="text-sm">Месяц</div>
              <div className="text-2xl font-semibold">{monthTotal} ккал</div>
              <div className={`text-xs ${theme.muted}`}>Средне/день: {avgPerDay.toFixed(0)} ккал</div>
            </div>
          </motion.div>
        </div>

        <div className={`rounded-2xl ${theme.card} border border-black/5 p-4 mb-4`}>
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-lg font-semibold flex items-center gap-2"><Plus size={18}/>Добавить</h2>
            <div className={`text-xs ${theme.muted}`}>Ручной / ИИ / Фото</div>
          </div>
          <div className="grid md:grid-cols-3 gap-3 mb-2">
            <input placeholder="Описание: плов, салат…" value={addText} onChange={e=>setAddText(e.target.value)} className="px-3 py-2 rounded-xl border border-black/10 bg-transparent"/>
            <input placeholder="Калории (ручной)" value={addKcal} onChange={e=>setAddKcal(e.target.value)} className="px-3 py-2 rounded-xl border border-black/10 bg-transparent"/>
            <div className="flex gap-2">
              <button onClick={handleAddManual} className="px-4 py-2 rounded-xl border border-black/10">Сохранить</button>
              <button onClick={handleAiEstimate} className="px-4 py-2 rounded-xl border border-black/10 flex items-center gap-2"><BrainCircuit size={16}/>ИИ</button>
            </div>
          </div>

          <div className="flex gap-2 items-center">
            <label className="px-3 py-2 rounded-xl border border-black/10 cursor-pointer flex items-center gap-2">
              <Camera size={16}/> Фото чека
              <input type="file" accept="image/*" className="hidden" onChange={(e)=>onPickPhoto(e,'receipt')}/>
            </label>
            <label className="px-3 py-2 rounded-xl border border-black/10 cursor-pointer flex items-center gap-2">
              <Camera size={16}/> Фото блюда
              <input type="file" accept="image/*" className="hidden" onChange={(e)=>onPickPhoto(e,'dish')}/>
            </label>
            {uploading && <span className="text-sm">Загрузка…</span>}
          </div>

          {aiLoading && <div className="mt-2 text-sm">ИИ считает…</div>}
          {aiResult && (
            <div className="mt-3 text-sm">
              <div className="font-medium mb-1">Добавлено ИИ:</div>
              <ul className="list-disc pl-5">
                {aiResult.items.map((it,i)=> <li key={i}>{it.name} — {it.kcal} ккал ({it.grams} г)</li>)}
              </ul>
              <div className="mt-1">Итого: <b>{aiResult.total_kcal}</b> ккал</div>
            </div>
          )}
        </div>

        <div className={`rounded-2xl ${theme.card} border border-black/5 p-2`}>
          <div className="flex items-center justify-between px-2 py-2">
            <h3 className="text-lg font-semibold">Сегодняшние приёмы пищи</h3>
            <div className="text-sm px-2 py-1 rounded-lg border border-black/10">{meals.length}</div>
          </div>
          <div className="divide-y divide-black/5">
            {meals.length===0? <div className="p-4 text-sm">Пока пусто.</div> : meals.map(m=> (
              <div key={m.id} className="flex items-center justify-between p-3">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-xl bg-black/5 flex items-center justify-center text-sm">{m.time}</div>
                  <div>
                    <div className="font-medium">{m.item||'Без названия'}</div>
                    <div className="text-xs opacity-70">{m.kcal} ккал</div>
                  </div>
                </div>
                <button onClick={()=>deleteMeal(m.id)} className="p-2 rounded-xl border border-black/10"><Trash2 size={16}/></button>
              </div>
            ))}
          </div>
        </div>

        <div className="mt-4 grid md:grid-cols-2 gap-3">
          <button onClick={fetchCoach} className="px-4 py-3 rounded-2xl border border-black/10 flex items-center gap-2"><MessageSquareQuote size={18}/>Совет коуча</button>
          <button onClick={analyzeDay} className="px-4 py-3 rounded-2xl border border-black/10">Анализ дня</button>
        </div>

        {coachText && (
          <div className={`mt-3 rounded-2xl ${theme.card} border border-black/5 p-4 whitespace-pre-line text-sm`}>{coachText}</div>
        )}

        <div className="fixed bottom-3 left-0 right-0">
          <div className="max-w-3xl mx-auto px-4">
            <div className={`rounded-2xl ${theme.card} shadow-xl border border-black/10 p-3 flex items-center justify-between`}>
              <div className="flex items-center gap-3"><Settings size={18}/><div className="text-sm"><div className="font-medium">Цель на день</div><div className={`text-xs`}>Меняется командой бота /setgoal</div></div></div>
              <a href="tg://resolve?domain=BotFather" className="px-4 py-2 rounded-xl border border-black/10">Открыть бота</a>
            </div>
          </div>
        </div>
      </div>

      <style>{`:root{--tg-bg:#ffffff;--tg-text:#111827;--tg-hint:#6b7280;--tg-button:#111827}`}</style>
    </div>
  )
}
