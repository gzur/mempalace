import{e as n,j as a,L as l}from"./index-DAMKUbAJ.js";/**
 * @license lucide-react v0.474.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const c=[["path",{d:"m9 18 6-6-6-6",key:"mthhwq"}]],i=n("ChevronRight",c);function x({items:o}){return a.jsx("nav",{"aria-label":"breadcrumb",className:"flex flex-wrap items-center gap-1 text-[12px] text-[color:var(--color-muted)]",children:o.map((e,s)=>{const r=s===o.length-1;let t;return"to"in e&&!r?t=a.jsx(l,{to:e.to,params:e.params,className:"hover:text-[color:var(--color-accent)] hover:underline",children:e.label}):t=a.jsx("span",{className:r?"text-[color:var(--color-fg)]":void 0,children:e.label}),a.jsxs("span",{className:"flex items-center gap-1",children:[t,!r&&a.jsx(i,{size:12,"aria-hidden":"true"})]},s)})})}export{x as B};
