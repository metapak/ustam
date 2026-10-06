// Ustam functional icons: a shared 24px grid, rounded strokes, and inherited color.
const paths = {
 projects: 'M3 4h7v7H3z M14 4h7v7h-7z M3 15h7v6H3z M14 15h7v6h-7z',
 folder: 'M3 7V5a1 1 0 0 1 1-1h5l2 3h9a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V7Z M3 9h18',
 orchestra: 'M9 17V5l11-2v12 M9 8l11-2 M9 17c0 1.7-1.5 3-3.5 3S2 19 2 17.5 3.5 15 5.5 15 9 15.7 9 17Z M20 15c0 1.7-1.5 3-3.5 3S13 17 13 15.5s1.5-2.5 3.5-2.5 3.5.7 3.5 2Z',
 works: 'M8 5H5a1 1 0 0 0-1 1v14a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1V6a1 1 0 0 0-1-1h-3 M8 3h8v4H8z M8 12l2 2 5-5 M8 18h8',
 usage: 'M4 3v17h17 M8 16v-5 M13 16V7 M18 16V4',
 settings: 'M4 6h16 M4 12h16 M4 18h16 M8 4v4 M16 10v4 M10 16v4',
 refresh: 'M20 10a8 8 0 0 0-14-5L3 8 M3 3v5h5 M4 14a8 8 0 0 0 14 5l3-3 M21 21v-5h-5',
 plus: 'M12 5v14 M5 12h14',
 close: 'M6 6l12 12 M18 6 6 18',
 remove: 'M5 12h14',
 edit: 'm14 5 5 5 M4 20l5-1L21 7l-4-4L5 15l-1 5Z',
 copy: 'M9 8h11v13H9z M15 8V3H4v13h5',
 rename: 'M5 7h10 M10 7v13 M7 20h6 M18 3h4 M20 3v18 M18 21h4',
 trash: 'M3 6h18 M9 6V3h6v3 M5 6l1 15h12l1-15 M10 10v7 M14 10v7'
};

export function uiIcon(name) {
 const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
 for(const [key,value] of Object.entries({viewBox:'0 0 24 24',fill:'none',stroke:'currentColor','stroke-width':'1.7','stroke-linecap':'round','stroke-linejoin':'round','aria-hidden':'true',focusable:'false',class:'ui-icon'}))svg.setAttribute(key,value);
 const path=document.createElementNS(svg.namespaceURI,'path');
 path.setAttribute('d',paths[name]);svg.append(path);return svg;
}
