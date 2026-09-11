import {render,screen,waitFor,fireEvent,within} from '@testing-library/react'; import {vi,test,expect} from 'vitest'; import App from './App';
const dash={total_crates:3,active_crates:2,clean_crates:1,pending_issues:1,isolated_crates:0};
const baseIssue={crate_id:1,crate_code:'BX-002',crate_name:'原料周转箱',occurred_at:'2026-09-10T08:00:00'};
const issues={
 unwashed:[{id:7,...baseIssue,issue_type:'reuse_without_wash',reason:'周转箱未清洗即再次领用',status:'confirmed',resolution_note:'已核实',rectification_event_no:null}],
 expired:[{id:8,crate_id:2,crate_code:'BX-005',crate_name:'待检箱',occurred_at:'2026-09-10T08:00:00',issue_type:'expired_inspection',reason:'最近检查已超过30天有效期',status:'confirmed',resolution_note:'',rectification_event_no:null}],
 mixed:[
  {id:9,crate_id:3,crate_code:'BX-006',crate_name:'待处理箱',occurred_at:'2026-09-10T08:00:00',issue_type:'reuse_without_wash',reason:'周转箱未清洗即再次领用',status:'pending',resolution_note:'',rectification_event_no:null},
  {id:7,...baseIssue,issue_type:'reuse_without_wash',reason:'周转箱未清洗即再次领用',status:'confirmed',resolution_note:'',rectification_event_no:null},
 ],
};
const ok=(data:unknown)=>({ok:true,json:()=>Promise.resolve(data)});
const openIssues=async(list:any[])=>{
 global.fetch=vi.fn((url,opts)=>{const u=String(url);
  if(u.includes('/api/issues'))return Promise.resolve(ok(list));
  return Promise.resolve(ok(u.includes('dashboard')?dash:[]))}) as any;
 render(<App/>);
 await waitFor(()=>expect(screen.getByText('周转箱总数')).toBeInTheDocument());
 fireEvent.click(screen.getByRole('button',{name:'问题中心'}));
 await waitFor(()=>expect(screen.getByText('BX-002')).toBeInTheDocument());
};
test('register rectification prefills wash, submits issue_id and returns to issue center closed',async()=>{
 let closed=false;const bodies:any[]=[];
 const list=issues.unwashed.map(x=>({...x}));
 global.fetch=vi.fn((url,opts)=>{const u=String(url);
  if(u.includes('/api/events')&&opts?.method==='POST'){const b=JSON.parse(String(opts.body));bodies.push(b);closed=true;list[0]={...list[0],status:'closed',rectification_event_no:'EV-W-1'};return Promise.resolve(ok({id:50,event_no:'EV-W-1'}))}
  if(u.includes('/api/issues'))return Promise.resolve(ok(list.map(x=>({...x}))));
  return Promise.resolve(ok(u.includes('dashboard')?dash:[]))}) as any;
 render(<App/>);
 await waitFor(()=>expect(screen.getByText('周转箱总数')).toBeInTheDocument());
 fireEvent.click(screen.getByRole('button',{name:'问题中心'}));
 await waitFor(()=>expect(screen.getByText('BX-002')).toBeInTheDocument());
 const row=screen.getByText('BX-002').closest('tr') as HTMLElement;
 fireEvent.click(within(row).getByRole('button',{name:'登记整改'}));
 await waitFor(()=>expect(screen.getByText(/已按整改建议为周转箱 BX-002 预填清洗单/)).toBeInTheDocument());
 expect(bodies).toHaveLength(0);
 expect((screen.getByLabelText('周转箱编号') as HTMLInputElement).value).toBe('BX-002');
 expect((screen.getByLabelText('事件类型') as HTMLSelectElement).value).toBe('wash');
 expect((screen.getByLabelText('事件编号') as HTMLInputElement).value).toBe('');
 expect((screen.getByLabelText('操作人') as HTMLInputElement).value).toBe('');
 fireEvent.change(screen.getByLabelText('事件编号'),{target:{value:'EV-W-1'}});
 fireEvent.change(screen.getByLabelText('发生时间'),{target:{value:'2026-09-11T10:30'}});
 fireEvent.change(screen.getByLabelText('操作人'),{target:{value:'王工'}});
 fireEvent.click(screen.getByRole('button',{name:'提交事件'}));
 await waitFor(()=>expect(screen.getByText('整改登记成功，问题已关闭')).toBeInTheDocument());
 expect(bodies).toHaveLength(1);
 expect(bodies[0]).toMatchObject({event_no:'EV-W-1',crate_code:'BX-002',event_type:'wash',occurred_at:'2026-09-11T10:30',operator:'王工',issue_id:7});
 await waitFor(()=>expect(screen.getByText('整改事件：EV-W-1')).toBeInTheDocument());
 expect(screen.getByText('问题清单')).toBeInTheDocument();
 expect(closed).toBe(true);
});
test('expired inspection issue prefills inspect and closes',async()=>{
 const bodies:any[]=[];
 let list=issues.expired.map(x=>({...x}));
 global.fetch=vi.fn((url,opts)=>{const u=String(url);
  if(u.includes('/api/events')&&opts?.method==='POST'){const b=JSON.parse(String(opts.body));bodies.push(b);list=list.map(x=>({...x,status:'closed',rectification_event_no:'EV-I-1'}));return Promise.resolve(ok({id:51}))}
  if(u.includes('/api/issues'))return Promise.resolve(ok(list));
  return Promise.resolve(ok(u.includes('dashboard')?dash:[]))}) as any;
 render(<App/>);
 await waitFor(()=>expect(screen.getByText('周转箱总数')).toBeInTheDocument());
 fireEvent.click(screen.getByRole('button',{name:'问题中心'}));
 await waitFor(()=>expect(screen.getByText('BX-005')).toBeInTheDocument());
 const row=screen.getByText('BX-005').closest('tr') as HTMLElement;
 fireEvent.click(within(row).getByRole('button',{name:'登记整改'}));
 await waitFor(()=>expect(screen.getByText(/预填检查单/)).toBeInTheDocument());
 expect((screen.getByLabelText('事件类型') as HTMLSelectElement).value).toBe('inspect');
 fireEvent.change(screen.getByLabelText('事件编号'),{target:{value:'EV-I-1'}});
 fireEvent.change(screen.getByLabelText('操作人'),{target:{value:'李工'}});
 fireEvent.click(screen.getByRole('button',{name:'提交事件'}));
 await waitFor(()=>expect(screen.getByText('整改登记成功，问题已关闭')).toBeInTheDocument());
 expect(bodies[0]).toMatchObject({event_no:'EV-I-1',crate_code:'BX-005',event_type:'inspect',operator:'李工',issue_id:8});
 await waitFor(()=>expect(screen.getByText('整改事件：EV-I-1')).toBeInTheDocument());
});
test('rectification rejected keeps form values and issue unchanged',async()=>{
 const list=issues.unwashed.map(x=>({...x}));
 global.fetch=vi.fn((url,opts)=>{const u=String(url);
  if(u.includes('/api/events')&&opts?.method==='POST')return Promise.resolve({ok:false,status:409,json:()=>Promise.resolve({detail:'事件类型与该问题的整改建议不符'})});
  if(u.includes('/api/issues'))return Promise.resolve(ok(list.map(x=>({...x}))));
  return Promise.resolve(ok(u.includes('dashboard')?dash:[]))}) as any;
 render(<App/>);
 await waitFor(()=>expect(screen.getByText('周转箱总数')).toBeInTheDocument());
 fireEvent.click(screen.getByRole('button',{name:'问题中心'}));
 await waitFor(()=>expect(screen.getByText('BX-002')).toBeInTheDocument());
 fireEvent.click(within(screen.getByText('BX-002').closest('tr') as HTMLElement).getByRole('button',{name:'登记整改'}));
 await waitFor(()=>expect(screen.getByLabelText('事件编号')));
 fireEvent.change(screen.getByLabelText('事件编号'),{target:{value:'EV-W-X'}});
 fireEvent.change(screen.getByLabelText('操作人'),{target:{value:'王工'}});
 fireEvent.click(screen.getByRole('button',{name:'提交事件'}));
 await waitFor(()=>expect(screen.getByText('事件类型与该问题的整改建议不符')).toBeInTheDocument());
 // 表单保留，仍停留在登记页，可修正后重试
 expect((screen.getByLabelText('事件编号') as HTMLInputElement).value).toBe('EV-W-X');
 expect((screen.getByLabelText('操作人') as HTMLInputElement).value).toBe('王工');
 expect(screen.getByText('登记流转事件')).toBeInTheDocument();
 expect(screen.queryByText('整改事件：')).not.toBeInTheDocument();
});
test('only confirmed issues show rectify entry',async()=>{
 await openIssues(issues.mixed.map(x=>({...x})));
 const pendingRow=screen.getByText('BX-006').closest('tr') as HTMLElement;
 const confirmedRow=screen.getByText('BX-002').closest('tr') as HTMLElement;
 expect(within(pendingRow).queryByRole('button',{name:'登记整改'})).toBeNull();
 expect(within(confirmedRow).getByRole('button',{name:'登记整改'})).toBeInTheDocument();
});
test('ordinary single registration keeps original behavior and omits issue_id',async()=>{
 const bodies:any[]=[];
 global.fetch=vi.fn((url,opts)=>{const u=String(url);
  if(u.includes('/api/events')&&opts?.method==='POST'){bodies.push(JSON.parse(String(opts.body)));return Promise.resolve(ok({id:60}))}
  return Promise.resolve(ok(u.includes('dashboard')?dash:[]))}) as any;
 render(<App/>);
 await waitFor(()=>expect(screen.getByText('周转箱总数')).toBeInTheDocument());
 fireEvent.click(screen.getByRole('button',{name:'登记流转'}));
 fireEvent.change(screen.getByLabelText('事件编号'),{target:{value:'EV-PLAIN'}});
 fireEvent.change(screen.getByLabelText('周转箱编号'),{target:{value:'BX-001'}});
 fireEvent.change(screen.getByLabelText('发生时间'),{target:{value:'2026-09-11T08:00'}});
 fireEvent.change(screen.getByLabelText('操作人'),{target:{value:'赵工'}});
 fireEvent.click(screen.getByRole('button',{name:'提交事件'}));
 await waitFor(()=>expect(screen.getByText('流转事件登记成功')).toBeInTheDocument());
 expect(bodies).toHaveLength(1);
 expect(bodies[0]).toMatchObject({event_no:'EV-PLAIN',crate_code:'BX-001',event_type:'inbound',operator:'赵工'});
 expect(bodies[0]).not.toHaveProperty('issue_id');
});
