# StyleForge 分类结构文档（中英映射）

> 本文档由 `apps/api/styleforge/core/taxonomy.py` 的 `build_taxonomy()` 自动生成，与上传表单、检索分类保持单一数据源一致。
> 修改分类请编辑 `core/categories.py`（主分类）、`core/garment_attributes.py`（细分类规则）、`core/taxonomy.py`（中英标签）。

## 上传规则

- **大类（main category）必选**：上传衣物时必须从以下 27 个大类中选择一个。
- **细分类（subtype）可选**：大类选定后，细分类为可选项，可留空（留空时系统按大类进入检索槽位）。
- `other`（其他）为兜底大类，用于无法归入任何已知大类的衣物。

| # | key | 中文 | English | 细分类数量 | 可选的细分类 |
|---|-----|------|---------|-----------|--------------|
| 1 | `top` | 上装 | Top | 6 | 短款上衣(Crop Top)、卫衣/连帽衫(Hoodie)、毛衣/针织衫(Knitwear)、衬衫(Shirt)、T恤(T-Shirt)、背心/吊带(Tank Top) |
| 2 | `pants` | 裤装 | Pants | 5 | 牛仔裤(Jeans)、打底裤/紧身裤(Leggings)、短裤(Shorts)、西裤/正装裤(Tailored Trousers)、阔腿裤(Wide-Leg Pants) |
| 3 | `skirt` | 半身裙 | Skirt | 6 | A字裙(A-Line Skirt)、长裙(Maxi Skirt)、中长裙(Midi Skirt)、短裙(Mini Skirt)、铅笔裙(Pencil Skirt)、百褶裙(Pleated Skirt) |
| 4 | `dress` | 连衣裙 | Dress | 8 | A字连衣裙(A-Line Dress)、紧身连衣裙(Bodycon Dress)、鸡尾酒裙(Cocktail Dress)、晚礼服(Evening Dress)、长连衣裙(Maxi Dress)、中长连衣裙(Midi Dress)、短款连衣裙(Mini Dress)、衬衫裙(Shirt Dress) |
| 5 | `jumpsuit` | 连体装 | Jumpsuit | 1 | 阔腿连体裤(Wide-Leg Jumpsuit) |
| 6 | `outwear` | 外套 | Outerwear | 6 | 西装外套(Blazer)、大衣(Coat)、牛仔外套(Denim Jacket)、夹克(Jacket)、皮夹克(Leather Jacket)、风衣(Trench Coat) |
| 7 | `shoes` | 鞋 | Shoes | 9 | 踝靴/短靴(Ankle Boots)、靴子(Boots)、平底鞋/单鞋(Flats)、高跟鞋(High Heels)、乐福鞋(Loafers)、牛津鞋(Oxfords)、浅口鞋/船鞋(Pumps)、凉鞋/拖鞋(Sandals)、运动鞋/跑鞋(Sneakers) |
| 8 | `bag` | 包 | Bag | 6 | 双肩包(Backpack)、手拿包/晚宴包(Clutch)、斜挎包(Crossbody Bag)、邮差包(Satchel)、单肩包(Shoulder Bag)、托特包(Tote Bag) |
| 9 | `shorts` | 短裤 | Shorts | 1 | 短裤(Shorts) |
| 10 | `suit` | 西装 | Suit | 0 | — |
| 11 | `outfit_set` | 套装 | Outfit Set | 0 | — |
| 12 | `legwear` | 裤袜 | Legwear | 1 | 打底裤/紧身裤(Leggings) |
| 13 | `underwear` | 内衣 | Underwear | 0 | — |
| 14 | `sleepwear` | 睡衣 | Sleepwear | 0 | — |
| 15 | `swimwear` | 泳装 | Swimwear | 0 | — |
| 16 | `activewear_bra` | 运动内衣 | Activewear Bra | 0 | — |
| 17 | `belts` | 腰带 | Belts | 0 | — |
| 18 | `hats` | 帽子 | Hats | 3 | 针织帽(Beanie)、棒球帽(Cap)、礼帽/毡帽(Fedora) |
| 19 | `eyewear` | 眼镜 | Eyewear | 2 | 光学眼镜(Eyeglasses)、太阳镜(Sunglasses) |
| 20 | `earrings` | 耳饰 | Earrings | 3 | 垂坠耳环(Drop Earrings)、耳环(Hoop Earrings)、耳钉(Stud Earrings) |
| 21 | `necklace` | 项链 | Necklace | 2 | 锁骨链/项圈(Choker)、吊坠项链(Pendant Necklace) |
| 22 | `bracelet` | 手链 | Bracelet | 2 | 手镯(Bangle)、开口手镯(Cuff Bracelet) |
| 23 | `rings` | 戒指 | Rings | 1 | 个性戒指(Statement Ring) |
| 24 | `hairwear` | 头饰 | Hair Accessories | 2 | 发夹(Hair Clip)、发带(Headband) |
| 25 | `jewellery` | 首饰 | Jewellery | 0 | — |
| 26 | `accessory` | 配饰 | Accessory | 6 | 针织手套(Knit Gloves)、皮手套(Leather Gloves)、皮带手表(Leather Watch)、金属表链手表(Metal Watch)、围巾(Scarf)、披肩/围裹巾(Shawl) |
| 27 | `other` | 其他 | Other | 0 | — |

## 细分类明细（按大类分组）

### 上装（Top）· `top`

| key | 中文 | English |
|-----|------|---------|
| `crop_top` | 短款上衣 | Crop Top |
| `hoodie` | 卫衣/连帽衫 | Hoodie |
| `knitwear` | 毛衣/针织衫 | Knitwear |
| `shirt` | 衬衫 | Shirt |
| `t_shirt` | T恤 | T-Shirt |
| `tank_top` | 背心/吊带 | Tank Top |

### 裤装（Pants）· `pants`

| key | 中文 | English |
|-----|------|---------|
| `jeans` | 牛仔裤 | Jeans |
| `leggings` | 打底裤/紧身裤 | Leggings |
| `shorts` | 短裤 | Shorts |
| `tailored_trousers` | 西裤/正装裤 | Tailored Trousers |
| `wide_leg_pants` | 阔腿裤 | Wide-Leg Pants |

### 半身裙（Skirt）· `skirt`

| key | 中文 | English |
|-----|------|---------|
| `a_line_skirt` | A字裙 | A-Line Skirt |
| `maxi_skirt` | 长裙 | Maxi Skirt |
| `midi_skirt` | 中长裙 | Midi Skirt |
| `mini_skirt` | 短裙 | Mini Skirt |
| `pencil_skirt` | 铅笔裙 | Pencil Skirt |
| `pleated_skirt` | 百褶裙 | Pleated Skirt |

### 连衣裙（Dress）· `dress`

| key | 中文 | English |
|-----|------|---------|
| `a_line_dress` | A字连衣裙 | A-Line Dress |
| `bodycon_dress` | 紧身连衣裙 | Bodycon Dress |
| `cocktail_dress` | 鸡尾酒裙 | Cocktail Dress |
| `evening_dress` | 晚礼服 | Evening Dress |
| `maxi_dress` | 长连衣裙 | Maxi Dress |
| `midi_dress` | 中长连衣裙 | Midi Dress |
| `mini_dress` | 短款连衣裙 | Mini Dress |
| `shirt_dress` | 衬衫裙 | Shirt Dress |

### 连体装（Jumpsuit）· `jumpsuit`

| key | 中文 | English |
|-----|------|---------|
| `wide_leg_jumpsuit` | 阔腿连体裤 | Wide-Leg Jumpsuit |

### 外套（Outerwear）· `outwear`

| key | 中文 | English |
|-----|------|---------|
| `blazer` | 西装外套 | Blazer |
| `coat` | 大衣 | Coat |
| `denim_jacket` | 牛仔外套 | Denim Jacket |
| `jacket` | 夹克 | Jacket |
| `leather_jacket` | 皮夹克 | Leather Jacket |
| `trench_coat` | 风衣 | Trench Coat |

### 鞋（Shoes）· `shoes`

| key | 中文 | English |
|-----|------|---------|
| `ankle_boots` | 踝靴/短靴 | Ankle Boots |
| `boots` | 靴子 | Boots |
| `flats` | 平底鞋/单鞋 | Flats |
| `high_heels` | 高跟鞋 | High Heels |
| `loafers` | 乐福鞋 | Loafers |
| `oxfords` | 牛津鞋 | Oxfords |
| `pumps` | 浅口鞋/船鞋 | Pumps |
| `sandals` | 凉鞋/拖鞋 | Sandals |
| `sneakers` | 运动鞋/跑鞋 | Sneakers |

### 包（Bag）· `bag`

| key | 中文 | English |
|-----|------|---------|
| `backpack` | 双肩包 | Backpack |
| `clutch` | 手拿包/晚宴包 | Clutch |
| `crossbody_bag` | 斜挎包 | Crossbody Bag |
| `satchel` | 邮差包 | Satchel |
| `shoulder_bag` | 单肩包 | Shoulder Bag |
| `tote_bag` | 托特包 | Tote Bag |

### 短裤（Shorts）· `shorts`

| key | 中文 | English |
|-----|------|---------|
| `shorts` | 短裤 | Shorts |

### 西装（Suit）· `suit`

无细分类型（仅大类）。

### 套装（Outfit Set）· `outfit_set`

无细分类型（仅大类）。

### 裤袜（Legwear）· `legwear`

| key | 中文 | English |
|-----|------|---------|
| `leggings` | 打底裤/紧身裤 | Leggings |

### 内衣（Underwear）· `underwear`

无细分类型（仅大类）。

### 睡衣（Sleepwear）· `sleepwear`

无细分类型（仅大类）。

### 泳装（Swimwear）· `swimwear`

无细分类型（仅大类）。

### 运动内衣（Activewear Bra）· `activewear_bra`

无细分类型（仅大类）。

### 腰带（Belts）· `belts`

无细分类型（仅大类）。

### 帽子（Hats）· `hats`

| key | 中文 | English |
|-----|------|---------|
| `beanie` | 针织帽 | Beanie |
| `cap` | 棒球帽 | Cap |
| `fedora` | 礼帽/毡帽 | Fedora |

### 眼镜（Eyewear）· `eyewear`

| key | 中文 | English |
|-----|------|---------|
| `eyeglasses` | 光学眼镜 | Eyeglasses |
| `sunglasses` | 太阳镜 | Sunglasses |

### 耳饰（Earrings）· `earrings`

| key | 中文 | English |
|-----|------|---------|
| `drop_earrings` | 垂坠耳环 | Drop Earrings |
| `hoop_earrings` | 耳环 | Hoop Earrings |
| `stud_earrings` | 耳钉 | Stud Earrings |

### 项链（Necklace）· `necklace`

| key | 中文 | English |
|-----|------|---------|
| `choker` | 锁骨链/项圈 | Choker |
| `pendant_necklace` | 吊坠项链 | Pendant Necklace |

### 手链（Bracelet）· `bracelet`

| key | 中文 | English |
|-----|------|---------|
| `bangle` | 手镯 | Bangle |
| `cuff_bracelet` | 开口手镯 | Cuff Bracelet |

### 戒指（Rings）· `rings`

| key | 中文 | English |
|-----|------|---------|
| `statement_ring` | 个性戒指 | Statement Ring |

### 头饰（Hair Accessories）· `hairwear`

| key | 中文 | English |
|-----|------|---------|
| `hair_clip` | 发夹 | Hair Clip |
| `headband` | 发带 | Headband |

### 首饰（Jewellery）· `jewellery`

无细分类型（仅大类）。

### 配饰（Accessory）· `accessory`

| key | 中文 | English |
|-----|------|---------|
| `knit_gloves` | 针织手套 | Knit Gloves |
| `leather_gloves` | 皮手套 | Leather Gloves |
| `leather_watch` | 皮带手表 | Leather Watch |
| `metal_watch` | 金属表链手表 | Metal Watch |
| `scarf` | 围巾 | Scarf |
| `shawl` | 披肩/围裹巾 | Shawl |

### 其他（Other）· `other`

无细分类型（仅大类）。
