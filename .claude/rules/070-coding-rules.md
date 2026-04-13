# コーディング規約

## 警告とエラーの扱い

- 特に指定のない場合警告の抑制は絶対に許されない
- unchecked_cast警告をsuppressしてはいけない。型安全に変換できるように
  - @Suppress のアノテーションは許可なく利用してはいけない
- eslintの警告やエラーは無視してはいけない
  - eslint-disable* などの指定は一切許可しない

## ユニットテストについて

- シンボル名に日本語が利用できる処理系の場合はテストメソッド名を日本語にする
- テストメソッド名には "正しい" や "適切" などの曖昧な言葉を使わないこと。メソッド名は必ずテスト内容を言語化したものとすること

## mock用ライブラリの利用禁止

ユニットテストなどでmockライブラリがないと書けない・書きにくいケースはそもそも設計に問題があるケースが大半である。
設計を見直してテスタビリティを改善すべきであり、Mockライブラリの導入によって安易に解消することは一切を禁止する。

ライブラリの設計によってmockがないとテストがしにくいようなケースは、ライブラリのテストになってしまっていないかを確認したほうがよい。

### 限定的に許容されるケース

#### 依存の大きなテスタビリティの低いクラスのリファクタリング

リファクタリングの前段として全体をカバーするテストをまず追加するというのがプラクティスである。
依存が多くテスタビリティが低いクラスのリファクタリングの場合は、リファクタリングの前にmock系ライブラリを用いてカバレッジを向上させることは許容される。

リファクタリング後にテスタビリティが向上し、mockなしでもカバレッジが十分になった段階で速やかにmockライブラリの依存は排除すること。
pull requestをまたいでmockライブラリへの依存が残る状況は依然として許容しない。(mainへのマージ不可)

## 副作用の外部化

副作用（現在時刻の取得、乱数生成、外部API呼び出し、ファイルI/Oなど）は関数内部で直接実行せず、引数またはインターフェース経由で外部から注入すること。
関数を純粋に保つことでテスタビリティと再現性を確保する。

### 代表例

#### 現在時刻

```kotlin
// NG: 関数内で現在時刻を取得している
fun isExpired(token: Token): Boolean {
    val now = Instant.now()
    return token.expiresAt.isBefore(now)
}

// OK: 現在時刻を引数で受け取る
fun isExpired(token: Token, now: Instant): Boolean {
    return token.expiresAt.isBefore(now)
}
```

#### 乱数

```kotlin
// NG: 関数内で乱数を生成している
fun generateCode(): String {
    val random = Random()
    return (1..6).map { random.nextInt(10) }.joinToString("")
}

// OK: Random インスタンスを引数で受け取る
fun generateCode(random: Random): String {
    return (1..6).map { random.nextInt(10) }.joinToString("")
}
```

#### 外部API呼び出し

```kotlin
// NG: 関数内で直接HTTPリクエストを行っている
fun fetchUserName(userId: String): String {
    val response = httpClient.get("https://api.example.com/users/$userId")
    return response.body<UserResponse>().name
}

// OK: データ取得をインターフェースで抽象化する
fun interface UserRepository {
    fun findNameById(userId: String): String
}

fun fetchUserName(userId: String, repository: UserRepository): String {
    return repository.findNameById(userId)
}
```

### 判断基準

呼び出すたびに結果が変わりうる処理、または外部状態に依存する処理は、すべてこの原則の適用対象とする。
