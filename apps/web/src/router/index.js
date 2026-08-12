import { createRouter, createWebHistory } from 'vue-router'

const RecommendPage = () => import('../pages/RecommendPage.vue')
const WardrobePage = () => import('../pages/WardrobePage.vue')
const ImportPage = () => import('../pages/ImportPage.vue')
const SettingsPage = () => import('../pages/SettingsPage.vue')
const MemoriesPage = () => import('../pages/MemoriesPage.vue')

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/recommend' },
    { path: '/recommend', component: RecommendPage },
    { path: '/assistant', redirect: '/recommend' },
    { path: '/wardrobe', component: WardrobePage },
    { path: '/import', component: ImportPage },
    { path: '/memories', component: MemoriesPage },
    { path: '/settings', component: SettingsPage },
  ],
})
