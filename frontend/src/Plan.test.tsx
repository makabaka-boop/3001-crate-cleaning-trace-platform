import {render,screen,waitFor,fireEvent,within} from '@testing-library/react'; import {vi,test,expect} from 'vitest'; import App from './App';
const dash={total_crates:3,active_crates:2,clean_crates:1,pending_issues:2,isolated_crates:1};
const planItems=[
 {crate_id:1,code:'BX-NEW',name:'新箱',location:'仓库',last_inspected_at:null,due_date:null,days_remaining:null,category:'never_inspected'},
 {crate_id:2,code:'BX-OD',name:'旧箱',location:'待清洗区',last_inspected_at:'2026-08-01T08:00:00',due_date:'2026-08-31',days_remaining:-11,category:'overdue'},
 {crate_id:3,code:'BX-SOON',name:'鲜箱',location:'成品仓',last_inspected_at:'2026-08-20T08:00:00',due_date:'2026-09-19',days_remaining:8,category:'due_soon'},
];
const ok=(data:unknown)=>({ok:true,json:()=>Promise.resolve(data)});
const planRes=(items=planItems)=>ok({base_date:'2026-09-11',days_ahead:7,valid_days:30,total:items.length,items});
const openPlan=async()=>{render(<App/>);await waitFor(()=>expect(screen.getByText('周转箱总数')).toBeInTheDocument());fireEvent.click(screen.getByRole('button',{name:'进入检查计划'}));await waitFor(()=>expect(screen.getByText('BX-NEW')).toBeInTheDocument())};
test('opens plan from home and shows categories ordered never/overdue/due_soon',async()=>{
 const calls:string[]=[];
 global.fetch=vi.fn((url)=>{const u=String(url);if(u.includes('/inspection-plan')){calls.push(u);return Promise.resolve(planRes())}return Promise.resolve(ok(u.includes('dashboard')?dash:[]))}) as any;
 await openPlan();
 expect(calls).toHaveLength(1);
 expect(calls[0]).toContain('base_date=');expect(calls[0]).toContain('days_ahead=7');
 expect(screen.getByText('检查计划 · 检查有效期 30 天')).toBeInTheDocument();
 const rows=screen.getAllByRole('row').slice(1).map(r=>r.textContent||'');
 expect(rows).toHaveLength(3);
 expect(rows[0]).toContain('BX-NEW');expect(rows[0]).toContain('从未检查');
 expect(rows[1]).toContain('BX-OD');expect(rows[1]).toContain('已过期');expect(rows[1]).toContain('超期 11 天');
 expect(rows[2]).toContain('BX-SOON');expect(rows[2]).toContain('即将到期');expect(rows[2]).toContain('剩 8 天');
});
test('invalid base date or days keeps inputs and shows fixable hint without querying',async()=>{
 const calls:string[]=[];
 global.fetch=vi.fn((url)=>{const u=String(url);if(u.includes('/inspection-plan')){calls.push(u);return Promise.resolve(planRes())}return Promise.resolve(ok(u.includes('dashboard')?dash:[]))}) as any;
 await openPlan();
 expect(calls).toHaveLength(1);
 fireEvent.change(screen.getByLabelText('未来天数'),{target:{value:'-1'}});
 fireEvent.click(screen.getByRole('button',{name:'查询'}));
 await waitFor(()=>expect(screen.getByRole('alert')).toHaveTextContent('未来天数需为 0 至 365 的整数，请修正后重新查询'));
 expect((screen.getByLabelText('未来天数') as HTMLInputElement).value).toBe('-1');
 expect(calls).toHaveLength(1);
 fireEvent.change(screen.getByLabelText('未来天数'),{target:{value:'7'}});
 fireEvent.change(screen.getByLabelText('基准日期'),{target:{value:''}});
 fireEvent.click(screen.getByRole('button',{name:'查询'}));
 await waitFor(()=>expect(screen.getByRole('alert')).toHaveTextContent('请选择基准日期后再查询'));
 expect(calls).toHaveLength(1);
});
test('backend 422 shows fixable hint and keeps inputs and previous list',async()=>{
 global.fetch=vi.fn((url)=>{const u=String(url);
  if(u.includes('/inspection-plan')){if(u.includes('base_date=2026-09-01'))return Promise.resolve({ok:false,status:422,json:()=>Promise.resolve({detail:[{msg:'bad date'}]})});return Promise.resolve(planRes())}
  return Promise.resolve(ok(u.includes('dashboard')?dash:[]))}) as any;
 await openPlan();
 fireEvent.change(screen.getByLabelText('基准日期'),{target:{value:'2026-09-01'}});
 fireEvent.click(screen.getByRole('button',{name:'查询'}));
 await waitFor(()=>expect(screen.getByRole('alert')).toHaveTextContent('基准日期或未来天数不合法，请修正后重新查询'));
 expect((screen.getByLabelText('基准日期') as HTMLInputElement).value).toBe('2026-09-01');
 expect(screen.getByText('BX-NEW')).toBeInTheDocument();
});
test('register inspection prefills single form, submits via events api and crate exits plan',async()=>{
 let inspected=false;const bodies:any[]=[];
 global.fetch=vi.fn((url,opts)=>{const u=String(url);
  if(u.includes('/inspection-plan'))return Promise.resolve(planRes(inspected?planItems.filter(i=>i.code!=='BX-OD'):planItems));
  if(u.endsWith('/api/events')&&opts?.method==='POST'){bodies.push(JSON.parse(String(opts.body)));inspected=true;return Promise.resolve(ok({id:9}))}
  return Promise.resolve(ok(u.includes('dashboard')?dash:[]))}) as any;
 await openPlan();
 fireEvent.change(screen.getByLabelText('基准日期'),{target:{value:'2026-09-11'}});
 fireEvent.click(screen.getByRole('button',{name:'查询'}));
 await waitFor(()=>expect(screen.getByText('BX-OD')).toBeInTheDocument());
 const row=screen.getByText('BX-OD').closest('tr') as HTMLElement;
 fireEvent.click(within(row).getByRole('button',{name:'登记检查'}));
 await waitFor(()=>expect(screen.getByText(/已为周转箱 BX-OD 预填检查单/)).toBeInTheDocument());
 expect(bodies).toHaveLength(0);
 expect((screen.getByLabelText('周转箱编号') as HTMLInputElement).value).toBe('BX-OD');
 expect((screen.getByLabelText('事件类型') as HTMLSelectElement).value).toBe('inspect');
 expect((screen.getByLabelText('发生时间') as HTMLInputElement).value).toBe('2026-09-11T09:00');
 expect((screen.getByLabelText('事件编号') as HTMLInputElement).value).toBe('');
 fireEvent.change(screen.getByLabelText('事件编号'),{target:{value:'EV-900'}});
 fireEvent.change(screen.getByLabelText('操作人'),{target:{value:'王工'}});
 fireEvent.click(screen.getByRole('button',{name:'提交事件'}));
 await waitFor(()=>expect(screen.getByText('流转事件登记成功')).toBeInTheDocument());
 expect(bodies).toHaveLength(1);
 expect(bodies[0]).toMatchObject({event_no:'EV-900',crate_code:'BX-OD',event_type:'inspect',occurred_at:'2026-09-11T09:00',operator:'王工'});
 await waitFor(()=>expect(screen.queryByText('BX-OD')).not.toBeInTheDocument());
 expect(screen.getByText('BX-NEW')).toBeInTheDocument();
});
test('opening event page directly keeps the empty single form',async()=>{
 global.fetch=vi.fn((url)=>Promise.resolve(ok(String(url).includes('dashboard')?dash:[]))) as any;
 render(<App/>);
 await waitFor(()=>expect(screen.getByText('周转箱总数')).toBeInTheDocument());
 fireEvent.click(screen.getByRole('button',{name:'登记流转'}));
 expect((screen.getByLabelText('周转箱编号') as HTMLInputElement).value).toBe('');
 expect((screen.getByLabelText('发生时间') as HTMLInputElement).value).toBe('');
 expect((screen.getByLabelText('事件类型') as HTMLSelectElement).value).toBe('inbound');
});
