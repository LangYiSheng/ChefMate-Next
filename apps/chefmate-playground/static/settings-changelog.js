import {node} from './ui-components.js';

// Release content is maintained once on the server and rendered as plain text.
export function renderChangelog(host, data) {
  const releases=data.releases || [];
  if(!releases.length){
    host.append(node('p','settings-note','还没有发布更新记录。'));
    return;
  }
  for(const release of releases){
    const article=node('article','changelog-release');
    article.setAttribute('aria-label',`${release.version} 版本更新`);
    const header=node('header','changelog-header'),version=node('h2','changelog-version',release.version);
    header.append(version);
    if(release.version===data.current_version)header.append(node('span','changelog-current','当前版本'));
    const date=node('time','changelog-date',release.date);
    date.dateTime=release.date;
    header.append(date);
    article.append(header);
    if(release.source?.branch && release.source?.commit){
      const source=node('p','changelog-source'),commit=node('code','',release.source.commit.slice(0,7));
      commit.setAttribute('title',release.source.commit);
      source.append(node('span','',`分支 ${release.source.branch} · 提交 `),commit);
      article.append(source);
    }
    article.append(node('p','changelog-title',release.title));
    if(release.summary)article.append(node('p','changelog-summary',release.summary));
    for(const section of release.sections || []){
      const group=node('section','changelog-group'),list=node('ul','changelog-items');
      group.append(node('h3','',section.title));
      for(const item of section.items || [])list.append(node('li','',item));
      group.append(list);article.append(group);
    }
    host.append(article);
  }
}
