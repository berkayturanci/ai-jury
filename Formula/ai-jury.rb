class AiJury < Formula
  include Language::Python::Virtualenv

  desc "Cross-vendor multi-agent PR & code review jury"
  homepage "https://ai-jury.dev/"
  url "https://files.pythonhosted.org/packages/7f/70/b4d7aa232aaf3a151b107ab04ae67d28ee54058b985ea538083ad87d9954/ai_jury-1.15.0.tar.gz"
  sha256 "a2deb5d4ed63aa4930f68a6d379c684ced9afbf855761a547794bd6678073934"
  license "MIT"

  depends_on "python@3.13"

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "jury 1.15.0", shell_output("#{bin}/jury --version")
    assert_match "error: provide one of", shell_output("#{bin}/jury --mock 2>&1", 1)
  end
end
