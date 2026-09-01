<template>
  <el-config-provider :locale="zhCn">
    <el-container class="app-shell">
      <el-aside width="200px" class="sidebar">
        <div class="logo">👗 StyleForge</div>
        <el-menu :default-active="$route.path" router class="nav">
          <el-menu-item index="/recommend">✨ 穿搭推荐</el-menu-item>
          <el-menu-item index="/wardrobe">👕 我的衣柜</el-menu-item>
          <el-menu-item index="/import">📦 订单导入</el-menu-item>
          <el-menu-item index="/memories">🧠 偏好记忆</el-menu-item>
          <el-menu-item index="/settings">⚙️ 我的偏好</el-menu-item>
          <el-menu-item index="/health">◉ 系统状态</el-menu-item>
        </el-menu>
      </el-aside>
      <el-main>
        <router-view />
      </el-main>
    </el-container>
  </el-config-provider>
</template>

<script setup>
import zhCn from 'element-plus/es/locale/lang/zh-cn'
</script>

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

@media (max-width: 780px) {
  .app-shell {
    flex-direction: column;
  }
  .sidebar {
    width: 100% !important;
    border-right: 0;
    border-bottom: 1px solid var(--el-border-color-light);
  }
  .logo {
    padding-bottom: 8px;
  }
  .nav {
    display: flex;
    overflow-x: auto;
  }
  .nav :deep(.el-menu-item) {
    flex: 0 0 auto;
  }
}
</style>
