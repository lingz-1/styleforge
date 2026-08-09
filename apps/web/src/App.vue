<template>
  <el-container class="app-shell">
    <el-aside width="200px" class="sidebar">
      <div class="logo">👗 StyleForge</div>
      <el-menu :default-active="$route.path" router class="nav">
        <el-menu-item index="/recommend">✨ 穿搭推荐</el-menu-item>
        <el-menu-item index="/wardrobe">👕 我的衣柜</el-menu-item>
        <el-menu-item index="/import">📦 订单导入</el-menu-item>
        <el-menu-item index="/settings">⚙️ 我的偏好</el-menu-item>
      </el-menu>
    </el-aside>
    <el-main>
      <router-view />
    </el-main>
  </el-container>
</template>

<style scoped>
.app-shell {
  min-height: 100vh;
}
.sidebar {
  border-right: 1px solid var(--el-border-color-light);
}
.logo {
  font-size: 18px;
  font-weight: 700;
  padding: 16px 20px;
}
.nav {
  border-right: none;
}
</style>
