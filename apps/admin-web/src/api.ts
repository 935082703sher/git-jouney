let csrf='';
export function setCsrf(value:string){csrf=value;}
export async function api<T>(path:string,method='GET',body?:unknown):Promise<T>{
  const response=await fetch('/api'+path,{method,credentials:'include',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:body===undefined?undefined:JSON.stringify(body)});
  if(!response.ok){const data=await response.json().catch(()=>({detail:'Ошибка соединения'}));throw new Error(typeof data.detail==='string'?data.detail:'Проверьте заполненные поля');}
  return response.json();
}
export const date=(value:string|undefined,tz='Asia/Tashkent')=>value?new Intl.DateTimeFormat('ru-RU',{timeZone:tz,day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'}).format(new Date(value)):'—';
